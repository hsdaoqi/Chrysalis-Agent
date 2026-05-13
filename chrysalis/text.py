"""文本压缩小工具。"""


def brief_text(value: object, max_chars: int = 400) -> str:
    """保留短文本全文；长文本保留开头和结尾。

    超过 max_chars 时，保留前半段和后半段，中间用省略号连接。
    这样比单纯截断更容易看见结论、错误尾巴、路径等关键信息。
    """
    text = str(value)
    if len(text) <= max_chars:
        return text

    head = max_chars // 2
    tail = max_chars - head
    return text[:head] + "..." + text[-tail:]
