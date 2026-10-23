import json
from pathlib import Path

from hd.detector import PLATT_A, PLATT_B, THRESHOLD, format_sources

RESULTS = Path(__file__).resolve().parents[1] / "results"


def test_defaults_match_the_evaluated_run():
    metrics = json.loads((RESULTS / "ft_metrics.json").read_text())
    platt = metrics["calibration_test"]["platt_from_val"]
    assert THRESHOLD == metrics["threshold_from_val"]
    assert (PLATT_A, PLATT_B) == (platt["a"], platt["b"])


def test_format_sources_mirrors_ragtruth():
    assert format_sources(["only one"]) == "only one"
    assert format_sources(["a", "b"]) == "passage 1:a\n\npassage 2:b"
    assert format_sources(["a"], question="why?") == "Question: why?\n\na"
