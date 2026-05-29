"""文本压缩小工具。"""
import json
from typing import Any


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


def _truncate_tool_call_args_json(args: str, head_chars: int = 200) -> str:
    """
    Agent 调用工具写文件，args 包含了一个 5 万字的代码字符串。
    如果我们暴力截断：args[:500] + "...[truncated]"，
    生成的 JSON 就会缺胳膊少腿（比如缺少 }"）。
    API 收到这种破烂 JSON 会直接报 400 Bad Request，导致 Agent 陷入无限死循环崩溃

    这是一个能听懂 JSON 的截断器。它先 json.loads(args) 把字符串解析成 Python 字典，
    然后用递归函数 _shrink 深入字典内部，专门找到那些超长的字符串值，把它截短到 200 字，最后再 json.dumps() 重新打包。
    """
    try:
        parsed = json.loads(args)
    except (ValueError, TypeError):
        return args

    def _shrink(obj: Any) -> Any:
        if isinstance(obj, str):
            if len(obj) > head_chars:
                return obj[:head_chars] + "...[truncated]"
            return obj
        if isinstance(obj, dict):
            return {k: _shrink(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_shrink(v) for v in obj]
        return obj

    shrunken = _shrink(parsed)
    # ensure_ascii=False preserves CJK/emoji instead of bloating with \uXXXX
    return json.dumps(shrunken, ensure_ascii=False)
