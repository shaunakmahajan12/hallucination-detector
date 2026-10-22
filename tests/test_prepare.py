import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))
from prepare import split_sentences  # noqa: E402


def pieces(text):
    return [text[s:e] for s, e in split_sentences(text)]


def test_splits_on_terminal_punctuation():
    assert pieces("It rained. Then it stopped! Did it?") == ["It rained.", "Then it stopped!", "Did it?"]


def test_keeps_initials_and_abbreviations_together():
    assert pieces("Dr. Smith met J. K. Rowling. They talked.") == ["Dr. Smith met J. K. Rowling.", "They talked."]


def test_splits_on_newlines_and_drops_blank_lines():
    assert pieces("Overview:\n\n- Open late\n- Vegan options") == ["Overview:", "- Open late", "- Vegan options"]


def test_keeps_list_numbers_with_their_item():
    assert pieces("1. Mix it. 2. Bake it.\n3. Serve.") == ["1. Mix it.", "2. Bake it.", "3. Serve."]


def test_decimals_and_closing_quotes():
    assert pieces('It cost $3.50 today. He said "no." Fine.') == ["It cost $3.50 today.", 'He said "no."', "Fine."]


def test_offsets_cover_original_text():
    text = "  First one.  Second one.  "
    assert [text[s:e] for s, e in split_sentences(text)] == ["First one.", "Second one."]
