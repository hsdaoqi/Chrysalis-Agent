from chrysalis.history_display import (
    extract_text,
    normalize_final_text,
)

def test_display_text_helpers_strip_summaries() -> None:
    blocks = [{"type": "text", "text": "<summary>hidden</summary>\n\nVisible"}]

    assert normalize_final_text(extract_text(blocks)) == "Visible"
