import json
import sys
from pathlib import Path

from hd.aggrefact import build, windows

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
from evaluate import aggrefact_report  # noqa: E402

CLS, SEP = 1, 2


def test_windows_overlap_and_cover_the_document():
    doc = list(range(100))
    w = windows(doc, room=40, overlap=10)
    assert w[0] == doc[:40] and w[1][:10] == doc[30:40] and w[-1][-1] == 99
    assert windows(doc, room=200, overlap=10) == [doc]


def test_build_tracks_which_row_each_window_belongs_to():
    seqs, owner = build([[10], [11, 12]], [list(range(100, 130)), [200]], max_len=16, overlap=4, cls_id=CLS,
                        sep_id=SEP)
    assert owner == [0, 0, 0, 0, 1] and all(len(s) <= 16 for s in seqs)  # windows start at 0, 8, 16, 24
    assert seqs[-1] == [CLS, 11, 12, SEP, 200, SEP]


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_report_separates_out_of_domain_from_ragtruth(tmp_path):
    rows = [{"dataset": d, "label": y, "score": s, "windows": 1}
            for d in ("Wice", "RAGTruth") for y, s in ((0, 0.2), (0, 0.4), (1, 0.6), (1, 0.9))]
    write_jsonl(tmp_path / "aggrefact_dev.jsonl", rows)
    write_jsonl(tmp_path / "aggrefact_test.jsonl", rows)
    (tmp_path / "ft_metrics.json").write_text(json.dumps({"threshold_from_val": 0.5}))
    out = aggrefact_report(tmp_path)
    assert out["average_out_of_domain"]["datasets"] == 1 and out["average_all"]["datasets"] == 2
    assert out["per_dataset"]["Wice"]["bacc_ragtruth_threshold"] == 1.0
