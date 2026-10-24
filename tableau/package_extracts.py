"""Turn a packaged workbook with live CSV connections into one Tableau Public will accept.

Tableau Public only saves workbooks whose data comes from Hyper extracts. This reads a .twbx, writes one Hyper file
per CSV source (Data/Extracts/<name>.hyper, schema and table both "Extract"), and points each data source at its Hyper
file the way Tableau's own sample workbooks do: a hyper connection with an [Extract].[Extract] relation. Every field,
calculation and sheet keeps its name, so nothing else in the workbook changes.

  python tableau/package_extracts.py <in.twbx> <out.twbx>

Needs the Hyper API (pip install tableauhyperapi), which has no wheels for the newest Python, so use 3.11 or 3.12.
"""

import csv
import re
import sys
import tempfile
import zipfile
from pathlib import Path

from tableauhyperapi import (Connection, CreateMode, HyperProcess, Inserter, SqlType, TableDefinition, TableName,
                             Telemetry)

SQL = {"string": SqlType.text(), "real": SqlType.double(), "integer": SqlType.big_int()}
CAST = {"string": str, "real": float, "integer": int}
DATASOURCE = re.compile(r"    <datasource caption='([^']+)' inline='true' name='federated\.[^']+'.*?\n    </datasource>",
                        re.S)


def build_hyper(path: Path, csv_path: Path, cols: list[tuple[str, str]], hyper: HyperProcess) -> int:
    table = TableDefinition(TableName("Extract", "Extract"),
                            [TableDefinition.Column(name, SQL[dtype]) for name, dtype in cols])
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = [[CAST[dtype](r[name]) if r[name] != "" else None for name, dtype in cols] for r in csv.DictReader(f)]
    with Connection(hyper.endpoint, path, CreateMode.CREATE_AND_REPLACE) as conn:
        conn.catalog.create_schema("Extract")
        conn.catalog.create_table(table)
        with Inserter(conn, table) as ins:
            ins.add_rows(rows)
            ins.execute()
    return len(rows)


def to_hyper(block: str, caption: str) -> str:
    """Swap the textscan connection for a hyper one. Field names, captions and calculations are untouched."""
    old = re.search(r"name='(textscan\.\w+)'", block).group(1)
    new = "hyper." + old.split(".", 1)[1]
    block = block.replace(old, new)
    block = re.sub(r"<connection class='textscan' [^>]*/>",
                   f"<connection authentication='auth-none' author-locale='en_US' class='hyper' "
                   f"dbname='Data/Extracts/{caption}.hyper' default-settings='yes' port='' sslmode='' "
                   f"username='tableau_internal_user' />", block)
    block = re.sub(r"<relation connection='hyper\.\w+' name='[^']+' table='[^']+' type='table'>.*?</relation>",
                   f"<relation connection='{new}' name='Extract' table='[Extract].[Extract]' type='table' />",
                   block, flags=re.S)
    block = re.sub(r"\n\s*<metadata-record class='capability'>.*?</metadata-record>", "", block, flags=re.S)
    block = re.sub(r"<parent-name>\[[^\]]+\]</parent-name>", "<parent-name>[Extract]</parent-name>", block)
    block = re.sub(r"\n\s*<collation [^>]*/>", "", block)  # the CSV driver's collation, not Hyper's
    return re.sub(r"\n\s*<width>\d+</width>", "", block)


def main(src: Path, out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        twb = next(tmp.glob("*.twb"))
        xml = twb.read_bytes().decode("utf-8").replace("\r\n", "\n")
        (tmp / "Data" / "Extracts").mkdir(parents=True, exist_ok=True)

        with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper:
            for m in DATASOURCE.finditer(xml):
                block, caption = m.group(0), m.group(1)
                conn = re.search(r"<connection class='textscan' directory='([^']+)' filename='([^']+)'", block)
                if not conn:
                    continue
                cols = [(name, dtype) for dtype, name in
                        re.findall(r"<column datatype='(\w+)' name='([^']+)' ordinal='\d+' />",
                                   block.split("</relation>")[0])]
                rows = build_hyper(tmp / "Data" / "Extracts" / f"{caption}.hyper",
                                   tmp / conn.group(1) / conn.group(2), cols, hyper)
                xml = xml.replace(block, to_hyper(block, caption))
                print(f"{caption}: {rows:,} rows, {len(cols)} columns")

        if "textscan" in xml:
            sys.exit("a CSV connection is left in the workbook")
        twb.write_bytes(xml.replace("\n", "\r\n").encode("utf-8"))  # Tableau writes CRLF
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(twb, twb.name)
            for f in sorted((tmp / "Data" / "Extracts").glob("*.hyper")):
                z.write(f, f.relative_to(tmp))
    print(f"wrote {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
