1.


    这个错误的原因是：当你加载之前的会话并继续对话时，_drop_oldest_turn 在裁剪历史时可能删掉了 tool_result
    消息，但保留了对应的 assistant tool_calls 消息。OpenAI 协议要求每个 tool_calls 后面必须紧跟对应的 tool
    响应消息，缺一个都会报 400。

    根本原因在 context.py 的 _drop_oldest_turn 函数——它逐条删除最旧消息，但没有保证 tool_use/tool_result
    的配对完整性。当 assistant 消息含有 tool_calls 被保留，而下一条 user 消息（里面包含 tool_result blocks）被删掉或被
    _sanitize_leading_message 转成了普通文本时，转换到 OpenAI 格式后就会出现 tool_calls 没有对应 role: "tool"
    消息的情况。

    需要在 to_openai_messages 或 _drop_oldest_turn 中加一个保护：确保如果 assistant 消息有 tool_calls，其后必须有对应的
    tool result；否则要么一起删掉，要么把 tool_calls 字段移除。
