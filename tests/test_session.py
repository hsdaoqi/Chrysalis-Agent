from chrysalis.session import SessionContext


def test_session_context_remembers_recent_results():
    session = SessionContext(max_turns=2)

    session.remember("写讲稿", {"ok": True, "final": "已写入 D:\\桌面\\讲稿.txt"})
    session.remember("字数太少了", {"ok": True, "final": "已扩写"})

    context = session.context()
    assert "写讲稿" in context
    assert "D:\\桌面\\讲稿.txt" in context
    assert "字数太少了" in context


def test_session_context_drops_old_turns():
    session = SessionContext(max_turns=1)

    session.remember("旧任务", {"final": "旧结果"})
    session.remember("新任务", {"final": "新结果"})

    context = session.context()
    assert "旧任务" not in context
    assert "新任务" in context


def test_session_context_clear():
    session = SessionContext()
    session.remember("任务", {"final": "结果"})

    session.clear()

    assert session.context() == ""
