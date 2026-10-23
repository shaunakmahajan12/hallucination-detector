import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch):
    """Tests never inherit live API settings or a Hub token, so nothing reaches the network or the real model."""
    import os

    for name in list(os.environ):
        if name.startswith("HD_") or name in ("HF_TOKEN", "GEMINI_API_KEY"):
            monkeypatch.delenv(name)
