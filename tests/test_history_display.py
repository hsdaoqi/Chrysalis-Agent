from chrysalis.history_display import (
    extract_text,
    normalize_final_text,
    visible_user_text,
)

def test_display_text_helpers_strip_summaries() -> None:
    blocks = [{"type": "text", "text": "<summary>hidden</summary>\n\nVisible"}]

    assert normalize_final_text(extract_text(blocks)) == "Visible"


def test_visible_user_text_hides_orphaned_tool_result_text() -> None:
    message = {
        "role": "user",
        "blocks": [{
            "type": "text",
            "text": "[orphaned tool result converted to text]\n{\"ok\": false}",
        }],
    }

    assert visible_user_text(message) == ""
