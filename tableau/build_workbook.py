"""Build the evaluation dashboard into a Tableau workbook (.twb) that already has the 8 CSV connections.

Usage: python3 tableau/build_workbook.py <base.twb> <out.twb>

The base workbook only needs each tableau/data/*.csv connected as its own data source (Tableau writes those
blocks, with their generated internal names). Everything else is replaced: the Threshold parameter, the calculated
fields on ragtruth_predictions, nine worksheets, one fixed-size dashboard and the window list. Line endings and
Tableau's own datasource blocks are kept as they are. Standard library only.
"""

import re
import sys
import uuid
import xml.etree.ElementTree as ET

P = "[Parameters].[Parameter 1]"


def esc(v: str) -> str:
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;")
            .replace('"', "&quot;"))


def uid() -> str:
    return "{" + str(uuid.uuid4()).upper() + "}"


# ------------------------------------------------------------------ parameter and calculated fields

PARAM_DS = f"""    <datasource hasconnection='false' inline='true' name='Parameters' version='18.1'>
      <aliases enabled='yes' />
      <column caption='Threshold' datatype='real' default-format='n#,##0.000' name='[Parameter 1]' param-domain-type='range' role='measure' type='quantitative' value='0.650467'>
        <calculation class='tableau' formula='0.650467' />
        <range max='1.' min='0.' />
      </column>
    </datasource>
"""

CALCS = [  # id, caption, datatype, default format, formula
    ("1001", "Flagged", "integer", None, f"IIF([score] >= {P}, 1, 0)"),
    ("1002", "TP", "integer", None, "SUM(IIF([Calculation_1001] = 1 AND [label] = 1, 1, 0))"),
    ("1003", "FP", "integer", None, "SUM(IIF([Calculation_1001] = 1 AND [label] = 0, 1, 0))"),
    ("1004", "FN", "integer", None, "SUM(IIF([Calculation_1001] = 0 AND [label] = 1, 1, 0))"),
    ("1005", "Precision", "real", "p0.0%", "[Calculation_1002] / ([Calculation_1002] + [Calculation_1003])"),
    ("1006", "Recall", "real", "p0.0%", "[Calculation_1002] / ([Calculation_1002] + [Calculation_1004])"),
    ("1007", "F1", "real", "p0.0%",
     "2 * [Calculation_1002] / (2 * [Calculation_1002] + [Calculation_1003] + [Calculation_1004])"),
    ("1008", "Hallucination Rate", "real", "p0.0%", "AVG([label])"),
]


def calc_col(cid: str, indent: str = "      ") -> str:
    _, cap, dt, fmt, formula = next(c for c in CALCS if c[0] == cid)
    f = f" default-format='{fmt}'" if fmt else ""
    return (f"{indent}<column caption='{esc(cap)}' datatype='{dt}'{f} name='[Calculation_{cid}]' role='measure' "
            f"type='quantitative'>\n{indent}  <calculation class='tableau' formula='{esc(formula)}' />\n{indent}</column>\n")


# ------------------------------------------------------------------ worksheet builder

class Sheet:
    """One worksheet on one data source. Fields are referenced by column-instance names: none:<col>:nk
    (discrete dimension), none:<col>:qk (continuous dimension), sum:<col>:qk, usr:Calculation_N:qk."""

    def __init__(self, name: str, ds: str, title: str):
        self.name, self.ds, self.title = name, ds, title
        self.instances: dict[str, tuple[str, str, str]] = {}  # instance name -> (column, derivation, type)
        self.calcs: set[str] = set()
        self.rows = self.cols = ""
        self.mark = "Automatic"
        self.enc: list[tuple[str, str]] = []
        self.filters: list[str] = []
        self.slices: list[str] = []
        self.sorts: list[str] = []
        self.aggregate = True
        self.labels = False
        self.uses_param = False
        self.font = None  # (cell, header) font sizes for text tables
        self.header_width = None  # (field reference, pixels)

    def f(self, inst: str) -> str:
        """Register a column instance and return its fully qualified reference."""
        if inst in (":Measure Names", "Multiple Values"):
            return f"[{self.ds}].[{inst}]"
        deriv, col, kind = inst.split(":")
        derivation = {"none": "None", "sum": "Sum", "usr": "User", "avg": "Avg"}[deriv]
        typ = "nominal" if kind == "nk" else "quantitative"
        self.instances[inst] = (col, derivation, typ)
        if col.startswith("Calculation_"):
            self.calcs.add(col.split("_")[1])
            if col in ("Calculation_1001",) or col.split("_")[1] in {"1002", "1003", "1004", "1005", "1006", "1007"}:
                self.uses_param = True
        return f"[{self.ds}].[{inst}]"

    def keep(self, inst: str, *members: str) -> None:
        ref = self.f(inst)
        level = f"[{inst}]"
        if len(members) == 1:
            gf = (f"<groupfilter function='member' level='{esc(level)}' member='{esc(chr(34) + members[0] + chr(34))}' "
                  f"user:ui-domain='database' user:ui-enumeration='inclusive' user:ui-marker='enumerate' />")
        else:
            inner = "".join(f"<groupfilter function='member' level='{esc(level)}' member='{esc(chr(34) + m + chr(34))}' />"
                            for m in members)
            gf = (f"<groupfilter function='union' user:ui-domain='database' user:ui-enumeration='inclusive' "
                  f"user:ui-marker='enumerate'>{inner}</groupfilter>")
        self.filters.append(f"<filter class='categorical' column='{esc(ref)}'>{gf}</filter>")
        self.slices.append(ref)

    def measures(self, *insts: str) -> None:
        """Measure Names / Measure Values over the given measures."""
        refs = [self.f(i) for i in insts]
        inner = "".join(f"<groupfilter function='member' level='[:Measure Names]' member='{esc(chr(34) + r + chr(34))}' />"
                        for r in refs)
        mn = self.f(":Measure Names")
        self.filters.append(f"<filter class='categorical' column='{esc(mn)}'><groupfilter function='union' "
                            f"user:op='manual'>{inner}</groupfilter></filter>")
        self.slices.append(mn)

    def xml(self, ds_columns: dict[str, str]) -> str:
        deps = []
        base = {col for col, _, _ in self.instances.values() if not col.startswith("Calculation_")}
        if self.calcs:  # the calculated fields read these raw columns, which must be declared too
            base |= {"score", "label"}
        for col in sorted(base):
            if col in ds_columns:
                deps.append(ds_columns[col])
        deps = sorted(set(deps)) + [calc_col(c, "            ").rstrip("\n") for c in sorted(self.calcs)]
        if self.calcs & {"1005", "1006", "1007"}:  # these reference TP / FP / FN, which reference Flagged
            for c in ("1001", "1002", "1003", "1004"):
                if c not in self.calcs:
                    deps.append(calc_col(c, "            ").rstrip("\n"))
        inst_xml = [f"            <column-instance column='[{col}]' derivation='{d}' name='[{esc(i)}]' pivot='key' "
                    f"type='{t}' />" for i, (col, d, t) in sorted(self.instances.items())]
        param_dep = ""
        param_src = ""
        if self.uses_param:
            # no <datasource name='Parameters' /> in the view list: Tableau 2026.3 then drops every field
            # that reads the parameter (found by diagnostic workbooks). The dependency block alone is enough.
            param_dep = ("          <datasource-dependencies datasource='Parameters'>\n"
                         "            <column caption='Threshold' datatype='real' default-format='n#,##0.000' name='[Parameter 1]' "
                         "param-domain-type='range' role='measure' type='quantitative' value='0.650467'>\n"
                         "              <calculation class='tableau' formula='0.650467' />\n"
                         "              <range max='1.' min='0.' />\n"
                         "            </column>\n          </datasource-dependencies>\n")
        enc = "".join(f"<{k} column='{esc(v)}' />" for k, v in self.enc)
        label_style = ("<style><style-rule element='mark'><format attr='mark-labels-show' value='true' />"
                       "</style-rule></style>") if self.labels else ""
        slices = ("          <slices>" + "".join(f"<column>{esc(s)}</column>" for s in self.slices) + "</slices>\n"
                  if self.slices else "")
        return (
            f"    <worksheet name='{esc(self.name)}'>\n"
            f"      <layout-options><title><formatted-text><run bold='true' fontsize='11'>{esc(self.title)}</run>"
            f"</formatted-text></title></layout-options>\n"
            f"      <table>\n        <view>\n          <datasources>\n{param_src}"
            f"            <datasource caption='{esc(self.caption)}' name='{esc(self.ds)}' />\n          </datasources>\n"
            f"{param_dep}"
            f"          <datasource-dependencies datasource='{esc(self.ds)}'>\n" + "\n".join(deps + inst_xml) +
            f"\n          </datasource-dependencies>\n"
            + "".join(f"          {x}\n" for x in self.filters + self.sorts) + slices +
            f"          <aggregation value='{'true' if self.aggregate else 'false'}' />\n        </view>\n"
            + ("        <style>"
               + (f"<style-rule element='cell'><format attr='font-size' value='{self.font[0]}' /></style-rule>"
                  if self.font else "")
               + "<style-rule element='header'>"
               + (f"<format attr='font-size' value='{self.font[1]}' />" if self.font else "")
               + (f"<format attr='width' field='{esc(self.header_width[0])}' value='{self.header_width[1]}' />"
                  if self.header_width else "") + "</style-rule></style>\n"
               if self.font or self.header_width else "        <style />\n") +
            f"        <panes>\n          <pane selection-relaxation-option='selection-relaxation-allow'>\n"
            f"            <view><breakdown value='auto' /></view>\n            <mark class='{self.mark}' />\n"
            + (f"            <encodings>{enc}</encodings>\n" if enc else "") +
            (f"            {label_style}\n" if label_style else "") +
            f"          </pane>\n        </panes>\n"
            f"        <rows>{esc(self.rows)}</rows>\n        <cols>{esc(self.cols)}</cols>\n      </table>\n"
            f"      <simple-id uuid='{uid()}' />\n    </worksheet>\n")


def sheets(ds: dict[str, str]) -> list[Sheet]:
    out = []

    def new(name: str, caption: str, title: str) -> Sheet:
        s = Sheet(name, ds[caption], title)
        s.caption = caption
        out.append(s)
        return s

    s = new("KPIs", "headline", "Full RAGTruth test: 18,965 sentences")
    s.keep("none:scope:nk", "Full RAGTruth test")
    s.rows = s.f("none:method:nk")
    s.measures("sum:f1:qk", "sum:auroc:qk", "sum:precision:qk", "sum:recall:qk")
    s.cols = s.f(":Measure Names")
    s.mark = "Text"
    s.enc = [("text", s.f("Multiple Values"))]
    s.font = (14, 11)
    s.header_width = (s.f("none:method:nk"), 210)

    s = new("Gemini Sample", "headline", "Gemini judge sample: same 987 sentences (70 hallucinated)")
    s.keep("none:scope:nk", "Gemini sample")
    s.rows = s.f("none:method:nk")
    s.measures("sum:f1:qk", "sum:precision:qk", "sum:recall:qk")
    s.cols = s.f(":Measure Names")
    s.mark = "Text"
    s.enc = [("text", s.f("Multiple Values"))]
    s.font = (14, 11)
    s.header_width = (s.f("none:method:nk"), 210)

    s = new("Threshold Explorer", "threshold_curve", "Precision, recall and F1 by threshold (F1 peaks near 0.65)")
    s.cols = s.f("none:threshold:qk")
    s.measures("sum:precision:qk", "sum:recall:qk", "sum:f1:qk")
    s.rows = s.f("Multiple Values")
    s.mark = "Line"
    s.enc = [("color", s.f(":Measure Names"))]

    s = new("Live Metrics", "ragtruth_predictions", "At the slider threshold (fine-tuned model)")
    s.keep("none:method:nk", "Fine-tuned ModernBERT")
    s.measures("usr:Calculation_1005:qk", "usr:Calculation_1006:qk", "usr:Calculation_1007:qk",
               "sum:Calculation_1001:qk")
    s.rows = s.f(":Measure Names")
    s.mark = "Text"
    s.enc = [("text", s.f("Multiple Values"))]
    s.font = (22, 11)

    for name, col, title in (("By Task Type", "task_type", "F1 at the slider threshold, by task type (click to filter)"),
                             ("By Generator LLM", "generator_llm", "F1 at the slider threshold, by answer-writing LLM")):
        s = new(name, "ragtruth_predictions", title)
        s.keep("none:method:nk", "Fine-tuned ModernBERT")
        s.rows = s.f(f"none:{col}:nk")
        s.cols = s.f("usr:Calculation_1007:qk")
        s.mark = "Bar"
        s.enc = [("text", s.f("usr:Calculation_1007:qk")), ("tooltip", s.f("usr:Calculation_1006:qk")),
                 ("tooltip", s.f("usr:Calculation_1008:qk"))]
        s.labels = True

    s = new("LLM-AggreFact", "aggrefact_summary",
            "LLM-AggreFact balanced accuracy by dataset (mean 71.5%)")
    s.rows = s.f("none:dataset:nk")
    s.cols = s.f("sum:bacc_dev_threshold:qk")
    s.sorts.append(f"<sort class='computed' column='{esc(s.f('none:dataset:nk'))}' direction='DESC' "
                   f"using='{esc(s.f('sum:bacc_dev_threshold:qk'))}' />")
    s.mark = "Bar"
    s.enc = [("color", s.f("none:domain:nk")), ("text", s.f("sum:bacc_dev_threshold:qk")),
             ("tooltip", s.f("sum:auroc:qk")), ("tooltip", s.f("sum:claims:qk"))]
    s.labels = True

    s = new("Calibration", "calibration_bins", "Calibration on RAGTruth test (ECE 0.043 raw, 0.016 after Platt scaling)")
    s.aggregate = False
    s.cols = s.f("none:mean_score:qk")
    s.rows = s.f("none:observed_rate:qk")
    s.mark = "Line"
    s.enc = [("color", s.f("none:version:nk")), ("path", s.f("none:bin_mid:qk"))]

    s = new("Where It Misses", "error_rates", "Share of hallucinations caught, by hallucination type")
    s.keep("none:rate:nk", "Recall (hallucinations flagged)")
    s.keep("none:dimension:nk", "label_type")
    s.rows = s.f("none:slice:nk")
    s.cols = s.f("sum:value:qk")
    s.sorts.append(f"<sort class='computed' column='{esc(s.f('none:slice:nk'))}' direction='DESC' "
                   f"using='{esc(s.f('sum:value:qk'))}' />")
    s.mark = "Bar"
    s.enc = [("text", s.f("sum:value:qk")), ("tooltip", s.f("sum:sentences:qk"))]
    s.labels = True

    s = new("Speed", "speed", "Milliseconds per sentence (lower is faster)")
    s.rows = s.f("none:label:nk")
    s.cols = s.f("sum:ms_per_sentence:qk")
    s.sorts.append(f"<sort class='computed' column='{esc(s.f('none:label:nk'))}' direction='DESC' "
                   f"using='{esc(s.f('sum:ms_per_sentence:qk'))}' />")
    s.mark = "Bar"
    s.enc = [("text", s.f("sum:ms_per_sentence:qk"))]
    s.header_width = (s.f("none:label:nk"), 320)
    s.labels = True
    return out


# ------------------------------------------------------------------ dashboard and windows

W_PX, H_PX = 1200, 1882


def u(x: float, y: float, w: float, h: float) -> tuple[int, int, int, int]:
    """Pixels on the W_PX x H_PX canvas -> Tableau's 1/100000 zone units."""
    return (round(x * 100000 / W_PX), round(y * 100000 / H_PX), round(w * 100000 / W_PX), round(h * 100000 / H_PX))


# Read top-down like a report: result, explore a threshold, score behavior, robustness and failures, cost.
SECTIONS = [  # x, y, w, h in pixels, label
    ((0, 97, 1200, 22), "1. How good is it?"),
    ((0, 311, 1200, 22), "2. Pick a threshold"),
    ((0, 677, 1200, 22), "3. How the scores behave"),
    ((0, 1023, 1200, 22), "4. Does it generalize, and where does it fail?"),
    ((0, 1519, 1200, 22), "5. What it costs"),
]
SLIDER = (0, 333, 560, 42)
HINT_BOX = (580, 335, 620, 38)
LAYOUT = [(n, *u(*px)) for n, px in (  # sheet, x, y, w, h in pixels
    ("KPIs", (0, 119, 600, 190)),
    ("Gemini Sample", (600, 119, 600, 190)),
    ("Live Metrics", (0, 375, 360, 300)),
    ("By Task Type", (360, 375, 380, 300)),
    ("By Generator LLM", (740, 375, 460, 300)),
    ("Threshold Explorer", (0, 723, 600, 298)),
    ("Calibration", (600, 723, 600, 298)),
    ("LLM-AggreFact", (0, 1069, 600, 448)),
    ("Where It Misses", (600, 1069, 600, 448)),  # same box as LLM-AggreFact
    ("Speed", (0, 1541, 1200, 300)),
)]
NAVY, PAGE, CARD_BORDER = "#1f3a5f", "#f4f3ef", "#dcdad3"
SWATCH = {"blue": "#4e79a7", "orange": "#f28e2b", "red": "#e15759"}  # Tableau 10, the palette these sheets use
LEGENDS = [  # x, y, w, h in pixels, then (color, label) pairs; each sits just above its chart
    ((0, 699, 600, 24), [("orange", "Precision"), ("red", "Recall"), ("blue", "F1")]),
    ((600, 699, 600, 24), [("red", "Raw scores"), ("orange", "Platt-calibrated"), ("blue", "Perfect calibration")]),
    ((0, 1045, 600, 24), [("blue", "In-domain: RAGTruth (seen in training)"), ("orange", "Out-of-domain: 10 datasets")]),
]
TITLE = "RAG Hallucination Detector: evaluation dashboard"
HINT = ("Drag the slider to move the decision threshold. The three panels below update. "
        "Click a task bar to filter the LLM chart and live metrics to that task.")
SUB = ("ModernBERT-base fine-tuned on 100K RAGTruth sentence pairs. github.com/shaunakmahajan12/hallucination-detector, "
       "model: huggingface.co/shaunak-12/hallucination-detector-modernbert")
CAPTION = ("Data: RAGTruth test and LLM-AggreFact test predictions saved in the repo's results/, exported by "
           "scripts/export_tableau.py. The Gemini judge was scored on a 987-sentence sample of the test set.")


def actions(sheet_list: list) -> str:
    keep = {"By Generator LLM", "Live Metrics"}
    exclude = ",".join(x.name for x in sheet_list if x.name not in keep and x.name != "By Task Type")
    return ("  <actions>\n    <action caption='Filter by task type' name='[Action1_task_filter]'>\n"
            "      <activation auto-clear='true' type='on-select' />\n"
            "      <source dashboard='Evaluation' type='sheet' worksheet='By Task Type' />\n"
            "      <command command='tsc:tsl-filter'>\n        <param name='special-fields' value='all' />\n"
            f"        <param name='target' value='Evaluation' />\n        <param name='exclude' value='{esc(exclude)}' />\n"
            "      </command>\n    </action>\n  </actions>\n")


def dashboard() -> str:
    zid = iter(range(2, 200))
    tx, ty, tw, th = u(0, 0, 1200, 95)
    sx, sy, sw, sh = u(*SLIDER)
    hx, hy, hw, hh = u(*HINT_BOX)
    zones = [f"<zone h='{th}' id='{next(zid)}' type-v2='text' w='{tw}' x='{tx}' y='{ty}'><formatted-text>"
             f"<run bold='true' fontcolor='#ffffff' fontsize='20'>{esc(TITLE)}</run><run>Æ&#10;</run>"
             f"<run fontcolor='#c9d6e8' fontsize='10'>{esc(SUB)}</run></formatted-text>"
             f"<zone-style><format attr='background-color' value='{NAVY}' /><format attr='padding' value='10' />"
             f"</zone-style></zone>",
             f"<zone h='{sh}' id='{next(zid)}' mode='slider' param='{esc(P)}' type-v2='paramctrl' w='{sw}' "
             f"x='{sx}' y='{sy}' />",
             f"<zone h='{hh}' id='{next(zid)}' type-v2='text' w='{hw}' x='{hx}' y='{hy}'><formatted-text>"
             f"<run fontcolor='#52514e' fontsize='9'>{esc(HINT)}</run></formatted-text></zone>"]
    card = (f"<zone-style><format attr='background-color' value='#ffffff' />"
            f"<format attr='border-color' value='{CARD_BORDER}' /><format attr='border-style' value='solid' />"
            "<format attr='border-width' value='1' /><format attr='margin' value='4' /><format attr='padding' value='6' />"
            "</zone-style>")
    zones += [f"<zone h='{h}' id='{next(zid)}' name='{esc(n)}' w='{w}' x='{x}' y='{y}'>{card}</zone>"
              for n, x, y, w, h in LAYOUT]
    for px, label in SECTIONS:
        x, y, w, h = u(*px)
        zones.append(f"<zone h='{h}' id='{next(zid)}' type-v2='text' w='{w}' x='{x}' y='{y}'><formatted-text>"
                     f"<run bold='true' fontcolor='{NAVY}' fontsize='13'>{esc(label)}</run></formatted-text></zone>")
    for px, items in LEGENDS:
        x, y, w, h = u(*px)
        runs = "".join(f"<run fontcolor='{SWATCH[c]}' fontsize='12'>\u25a0 </run><run fontcolor='#3d3d3a' fontsize='10'>"
                       f"{esc(label)}      </run>" for c, label in items)
        zones.append(f"<zone h='{h}' id='{next(zid)}' type-v2='text' w='{w}' x='{x}' y='{y}'><formatted-text>{runs}"
                     f"</formatted-text></zone>")
    x, y, w, h = u(0, 1845, 1200, 34)
    zones.append(f"<zone h='{h}' id='{next(zid)}' type-v2='text' w='{w}' x='{x}' y='{y}'><formatted-text>"
                 f"<run fontcolor='#898781' fontsize='9'>{esc(CAPTION)}</run></formatted-text></zone>")
    return ("  <dashboards>\n    <dashboard name='Evaluation'>\n      <style />\n"
            f"      <size maxheight='{H_PX}' maxwidth='{W_PX}' minheight='{H_PX}' minwidth='{W_PX}' sizing-mode='fixed' />\n"
            "      <zones>\n        <zone h='100000' id='1' type-v2='layout-basic' w='100000' x='0' y='0'>\n"
            + "".join(f"          {z}\n" for z in zones) +
            # Tableau's schema wants zone-style after the child zones
            f"          <zone-style><format attr='background-color' value='{PAGE}' /></zone-style>\n"
            f"        </zone>\n      </zones>\n      <simple-id uuid='{uid()}' />\n    </dashboard>\n  </dashboards>\n")


CARDS = ("      <cards>\n        <edge name='left'><strip size='160'><card type='pages' /><card type='filters' />"
         "<card type='marks' /></strip></edge>\n        <edge name='top'><strip size='2147483647'><card type='columns' />"
         "</strip><strip size='2147483647'><card type='rows' /></strip><strip size='31'><card type='title' /></strip>"
         "</edge>\n{right}      </cards>\n")


def windows(sheet_list: list[Sheet]) -> str:
    out = ["  <windows source-height='30'>\n"]
    for s in sheet_list:
        right = (f"        <edge name='right'><strip size='160'><card param='{esc(P)}' type='parameter' /></strip></edge>\n"
                 if s.uses_param else "")
        out.append(f"    <window class='worksheet' hidden='true' name='{esc(s.name)}'>\n" + CARDS.format(right=right) +
                   f"      <simple-id uuid='{uid()}' />\n    </window>\n")
    out.append("    <window class='dashboard' maximized='true' name='Evaluation'>\n      <viewpoints>\n"
               + "".join(f"        <viewpoint name='{esc(s.name)}'><zoom type='entire-view' /></viewpoint>\n"
                         if s.mark in ("Bar", "Line", "Text") else f"        <viewpoint name='{esc(s.name)}' />\n"
                         for s in sheet_list) +
               f"      </viewpoints>\n      <active id='-1' />\n      <simple-id uuid='{uid()}' />\n    </window>\n")
    out.append("  </windows>\n")
    return "".join(out)


# ------------------------------------------------------------------ repair a mis-pointed CSV source

def csv_types(path: str) -> list[tuple[str, str]]:
    import csv
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    out = []
    for i, name in enumerate(rows[0]):
        vals = [r[i] for r in rows[1:] if r[i] != ""]
        kind = "string"
        if vals and all(re.fullmatch(r"-?\d+", v) for v in vals):
            kind = "integer"
        elif vals and all(re.fullmatch(r"-?\d+(\.\d+)?([eE]-?\d+)?", v) for v in vals):
            kind = "real"
        out.append((name, kind))
    return out


def textscan_source(caption: str, fed: str, directory: str, filename: str) -> str:
    """A fresh data source block for one CSV, in the shape Tableau writes (metadata records are re-read on open)."""
    conn = "textscan." + uuid.uuid4().hex[:28]
    table = f"[{filename.replace('.', '#')}]"
    obj = f"{filename}_{uuid.uuid4().hex.upper()}"
    cols = csv_types(f"{directory}/{filename}")
    rel_cols = "".join(f"            <column datatype='{t}' name='{esc(n)}' ordinal='{i}' />\n" for i, (n, t) in enumerate(cols))
    rel = (f"<relation connection='{conn}' name='{esc(filename)}' table='{esc(table)}' type='table'>\n"
           f"          <columns character-set='UTF-8' header='yes' locale='en_US' separator=','>\n{rel_cols}"
           f"          </columns>\n        </relation>")
    captions = "".join(
        f"      <column caption='{esc(n.replace('_', ' ').title())}' datatype='{t}' name='[{esc(n)}]' "
        f"role='{'dimension' if t == 'string' else 'measure'}' type='{'nominal' if t == 'string' else 'quantitative'}' />\n"
        for n, t in sorted(cols))
    return (f"    <datasource caption='{esc(caption)}' inline='true' name='{fed}' version='18.1'>\n"
            f"      <connection class='federated'>\n        <named-connections>\n"
            f"          <named-connection caption='{esc(caption)}' name='{conn}'>\n"
            f"            <connection class='textscan' directory='{esc(directory)}' filename='{esc(filename)}' password='' server='' />\n"
            f"          </named-connection>\n        </named-connections>\n        {rel}\n      </connection>\n"
            f"      <aliases enabled='yes' />\n"
            f"      <column caption='{esc(filename)}' datatype='table' name='[__tableau_internal_object_id__].[{obj}]' role='measure' type='quantitative' />\n"
            f"{captions}"
            f"      <layout dim-ordering='alphabetic' measure-ordering='alphabetic' show-structure='true' />\n"
            f"      <object-graph>\n        <objects>\n          <object caption='{esc(filename)}' id='{obj}'>\n"
            f"            <properties context=''>\n              {rel.replace(chr(10), chr(10) + '      ')}\n"
            f"            </properties>\n          </object>\n        </objects>\n      </object-graph>\n"
            f"    </datasource>\n")


def repair_sources(s: str) -> str:
    """Rebuild any source whose table points at a different CSV than its own file (a slip in New Data Source)."""
    for m in list(re.finditer(r"    <datasource caption='([^']+)' inline='true' name='([^']+)'.*?\n    </datasource>\n", s, re.S)):
        block = m.group(0)
        fn = re.search(r"<connection class='textscan' directory='([^']+)' filename='([^']+)'", block)
        rel = re.search(r"<relation connection='[^']+' name='([^']+)'", block)
        stale = False
        if fn and rel and rel.group(1) == fn.group(2):
            have = re.findall(r"<column datatype='[^']*' name='([^']+)' ordinal='\d+' />", block.split("</relation>")[0])
            stale = have != [n for n, _ in csv_types(f"{fn.group(1)}/{fn.group(2)}")]
            if stale:
                print(f"refreshing '{m.group(1)}': its CSV columns changed")
        if fn and rel and (rel.group(1) != fn.group(2) or stale):
            if not stale:
                print(f"repairing '{m.group(1)}': table was {rel.group(1)}, file is {fn.group(2)}")
            s = s.replace(block, textscan_source(m.group(1), m.group(2), fn.group(1), fn.group(2)))
    return s


# ------------------------------------------------------------------ assemble

def main() -> None:
    src, dst = sys.argv[1], sys.argv[2]
    raw = open(src, encoding="utf-8", newline="").read()
    crlf = "\r\n" in raw
    s = repair_sources(raw.replace("\r\n", "\n"))
    for old, cap in (("bacc_dev_threshold", "Balanced Accuracy"), ("value", "Share Caught")):
        s = re.sub(rf"<column caption='[^']*' datatype='real' name='\[{old}\]' role='measure'",
                   f"<column caption='{cap}' datatype='real' default-format='p0.0%' name='[{old}]' role='measure'", s)
    # headline table: F1, precision and recall as percentages, like the rest of the dashboard (AUROC stays a decimal)
    hs = s.index("<datasource caption='headline'")
    he = s.index("</datasource>", hs)
    block = s[hs:he]
    for col in ("f1", "precision", "recall"):
        block = re.sub(rf"(<column caption='[^']*' datatype='real')( name='\[{col}\]' role='measure')",
                       r"\1 default-format='p0.0%'\2", block)
    s = s[:hs] + block + s[he:]
    s = re.sub(r"<column caption='Slice' datatype='string' name='\[slice\]'",
               "<column caption='Hallucination Type' datatype='string' name='[slice]'", s)
    s = re.sub(r"<column caption='Label' datatype='string' name='\[label\]'",
               "<column caption='Method And Hardware' datatype='string' name='[label]'", s)

    # data sources by caption, and each one's <column .../> lines for datasource-dependencies
    ds, ds_cols = {}, {}
    for m in re.finditer(r"    <datasource caption='([^']+)' inline='true' name='([^']+)'.*?\n    </datasource>\n", s, re.S):
        ds[m.group(1)] = m.group(2)
        for c in re.finditer(r"\n      (<column caption='[^']*' datatype='[^']*'(?: default-format='[^']*')? name='\[([^\]]+)\]' role='[^']*' type='[^']*' />)",
                             m.group(0)):
            ds_cols.setdefault(m.group(2), {})[c.group(2)] = "            " + c.group(1)
    need = ["ragtruth_predictions", "headline", "threshold_curve", "aggrefact_summary", "calibration_bins",
            "error_rates", "speed"]
    missing = [n for n in need if n not in ds]
    if missing:
        sys.exit(f"base workbook is missing data sources: {missing}")

    # 1. parameter: replace whatever Parameters source exists with the ranged one
    s = re.sub(r"    <datasource hasconnection='false' inline='true' name='Parameters'.*?\n    </datasource>\n", "", s,
               flags=re.S)
    s = s.replace("  <datasources>\n", "  <datasources>\n" + PARAM_DS, 1)

    # 2. calculated fields on ragtruth_predictions (drop earlier copies first)
    start = s.index("<datasource caption='ragtruth_predictions'")
    end = s.index("\n    </datasource>", start)
    block = re.sub(r"      <column caption='[^']*' datatype='[^']*'( default-format='[^']*')? name='\[Calculation_\d+\]'.*?"
                   r"</column>\n", "", s[start:end], flags=re.S)
    layout = block.index("      <layout ")
    block = block[:layout] + "".join(calc_col(c[0]) for c in CALCS) + block[layout:]
    s = s[:start] + block + s[end:]

    # 3. worksheets, dashboard, windows; thumbnails dropped (Tableau regenerates them on save)
    sheet_list = sheets(ds)
    cols_by_ds = {name: ds_cols.get(name, {}) for name in ds.values()}
    ws = "  <worksheets>\n" + "".join(sh.xml(cols_by_ds[sh.ds]) for sh in sheet_list) + "  </worksheets>\n"
    head = s[:s.index("  <worksheets>")] if "  <worksheets>" in s else s[:s.index("</workbook>")]
    s = head + actions(sheet_list) + ws + dashboard() + windows(sheet_list) + "</workbook>\n"

    root = ET.fromstring(s.encode("utf-8"))  # well-formed check
    assert len(root.findall("./worksheets/worksheet")) == len(sheet_list)
    open(dst, "w", encoding="utf-8", newline="").write(s.replace("\n", "\r\n") if crlf else s)
    print(f"wrote {dst}: {len(sheet_list)} worksheets + dashboard 'Evaluation'")


if __name__ == "__main__":
    main()
