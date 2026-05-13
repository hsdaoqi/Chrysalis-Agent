from chrysalis.text import brief_text


def test_brief_text_keeps_short_text_whole():
    text = "短文本" * 20
    assert brief_text(text) == text


def test_brief_text_keeps_head_and_tail_for_long_text():
    text = "a" * 250 + "MIDDLE" + "z" * 250
    result = brief_text(text)

    assert result.startswith("a" * 200)
    assert result.endswith("z" * 200)
    assert "..." in result
    assert "MIDDLE" not in result
