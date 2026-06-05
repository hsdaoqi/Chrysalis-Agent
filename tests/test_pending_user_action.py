from chrysalis.kernel import Kernel


def test_ask_user_reply_resumes_original_task_and_clears_pending() -> None:
    kernel = object.__new__(Kernel)
    kernel.pending_user_action = {
        "task": "\u8bf7\u68c0\u67e5\u670d\u52a1\u72b6\u6001",
        "question": "\u8981\u91cd\u542f\u670d\u52a1\u5417\uff1f",
        "reason": "need_user",
        "result": {"need_user": True},
    }
    kernel._resume_prompt_internal = False

    run_task, context, immediate = Kernel._resolve_pending_user_action(kernel, "\u91cd\u542f\u5427")

    assert run_task == "\u8bf7\u68c0\u67e5\u670d\u52a1\u72b6\u6001"
    assert immediate is None
    assert "\u8981\u91cd\u542f\u670d\u52a1\u5417\uff1f" in context
    assert "\u91cd\u542f\u5427" in context
    assert kernel.pending_user_action is None
    assert kernel._resume_prompt_internal is True
