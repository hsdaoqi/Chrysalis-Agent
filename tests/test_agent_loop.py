from dataclasses import dataclass

from chrysalis.agent_loop import AgentLoop
from chrysalis.memory import Memory
from chrysalis.skills import SkillLibrary


@dataclass
class FakeChatResult:
    text: str


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.seen_messages = []

    def chat(self, messages):
        self.seen_messages.append(messages)
        return FakeChatResult(self.responses.pop(0))


def test_loop_runs_tool_then_final(tmp_path):
    memory = Memory(tmp_path / "memory", tmp_path / "data" / "memory.json")
    llm = FakeLLM([
        '{"tool":"file_write","args":{"path":"a.txt","content":"hello"}}',
        '{"final":"done"}',
    ])
    loop = AgentLoop(llm, memory, tmp_path / "workspace", max_turns=3)
    result = loop.run("写入一个文件")
    assert result["ok"] is True
    assert (tmp_path / "workspace" / "a.txt").read_text(encoding="utf-8") == "hello"


def test_loop_reports_each_turn_progress(tmp_path):
    events = []
    (tmp_path / "workspace").mkdir()
    memory = Memory(tmp_path / "memory", tmp_path / "data" / "memory.json")
    llm = FakeLLM([
        '{"tool":"file_list","args":{"path":"."},"thought":"先看目录"}',
        '{"final":"完成"}',
    ])
    loop = AgentLoop(llm, memory, tmp_path / "workspace", max_turns=3, progress=events.append)

    result = loop.run("看看目录")

    assert result["ok"] is True
    assert any("第 1 轮：调用工具 file_list" in item for item in events)
    assert any("第 1 轮：工具结果 ok=True" in item for item in events)
    assert any("第 2 轮：最终回答" in item for item in events)


def test_loop_injects_l1_and_runs_skill(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "write_note.py").write_text(
        """
def execute(task: str) -> dict:
    return {"ok": True, "message": "技能已执行"}
""",
        encoding="utf-8",
    )
    memory = Memory(tmp_path / "memory", tmp_path / "data" / "memory.json")
    memory.add_skill("write_note", "来源任务：写入 note 文件")
    llm = FakeLLM([
        '{"skill":"write_note","thought":"L1 里已有合适技能"}',
        '{"final":"完成"}',
    ])
    library = SkillLibrary(skills_dir, memory)
    loop = AgentLoop(llm, memory, tmp_path / "workspace", library, max_turns=3)

    result = loop.run("帮我写入 note 文件")

    assert result["ok"] is True
    assert "write_note" in llm.seen_messages[0][0]["content"]
    assert result["transcript"][1]["observation"]["message"] == "技能已执行"


def test_loop_keeps_final_when_memory_write_fails(tmp_path):
    class BrokenMemory(Memory):
        def remember_episode(self, task: str, result: str) -> None:
            raise ValueError("bad memory")

    memory = BrokenMemory(tmp_path / "memory", tmp_path / "data" / "memory.json")
    llm = FakeLLM(['{"final":"已经完成"}'])
    loop = AgentLoop(llm, memory, tmp_path / "workspace", max_turns=1)

    result = loop.run("测试记忆失败")

    assert result["ok"] is True
    assert result["final"] == "已经完成"
    assert "写入最近任务摘要失败" in result["transcript"][-1]["memory_warning"]


def test_loop_sends_compact_observation_to_next_turn(tmp_path):
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "big.txt").write_text("a" * 800 + "MIDDLE" + "z" * 800, encoding="utf-8")
    memory = Memory(tmp_path / "memory", tmp_path / "data" / "memory.json")
    llm = FakeLLM([
        '{"tool":"file_read","args":{"path":"big.txt"}}',
        '{"final":"完成"}',
    ])
    loop = AgentLoop(llm, memory, tmp_path / "workspace", max_turns=2)

    result = loop.run("读取大文件")

    assert result["ok"] is True
    observation_message = llm.seen_messages[1][-1]["content"]
    assert "MIDDLE" not in observation_message
    assert len(observation_message) < 1400


def test_loop_updates_working_checkpoint_and_injects_it(tmp_path):
    memory = Memory(tmp_path / "memory", tmp_path / "data" / "memory.json")
    llm = FakeLLM([
        '{"tool":"update_working_checkpoint","args":{"key_info":"已经确认 workspace 为空","related_sop":"plan_sop.md"}}',
        '{"tool":"file_list","args":{"path":"."}}',
        '{"final":"完成"}',
    ])
    loop = AgentLoop(llm, memory, tmp_path / "workspace", max_turns=3)

    result = loop.run("检查 workspace")

    assert result["ok"] is True
    second_prompt = llm.seen_messages[1][-1]["content"]
    assert "当前短期工作记忆" in second_prompt
    assert "已经确认 workspace 为空" in second_prompt


def test_loop_ask_user_interrupts_task(tmp_path):
    memory = Memory(tmp_path / "memory", tmp_path / "data" / "memory.json")
    llm = FakeLLM([
        '{"tool":"ask_user","args":{"question":"要继续吗？","candidates":["继续","停止"]}}',
    ])
    loop = AgentLoop(llm, memory, tmp_path / "workspace", max_turns=1)

    result = loop.run("需要用户确认")

    assert result["ok"] is False
    assert result["need_user"] is True
    assert result["question"] == "要继续吗？"
    assert result["candidates"] == ["继续", "停止"]


def test_loop_marks_long_term_update_request(tmp_path):
    memory = Memory(tmp_path / "memory", tmp_path / "data" / "memory.json")
    llm = FakeLLM([
        '{"tool":"start_long_term_update","args":{"reason":"发现了稳定路径"}}',
        '{"final":"完成"}',
    ])
    loop = AgentLoop(llm, memory, tmp_path / "workspace", max_turns=2)

    result = loop.run("沉淀经验")

    assert result["ok"] is True
    prompt = llm.seen_messages[1][-1]["content"]
    assert "long_term_update_requested" in prompt
    assert "发现了稳定路径" in prompt


def test_loop_resets_working_memory_between_runs(tmp_path):
    memory = Memory(tmp_path / "memory", tmp_path / "data" / "memory.json")
    llm = FakeLLM([
        '{"tool":"update_working_checkpoint","args":{"key_info":"第一轮临时状态"}}',
        '{"final":"第一轮完成"}',
        '{"tool":"file_list","args":{"path":"."}}',
        '{"final":"第二轮完成"}',
    ])
    loop = AgentLoop(llm, memory, tmp_path / "workspace", max_turns=2)

    first = loop.run("第一轮")
    second = loop.run("第二轮")

    assert first["ok"] is True
    assert second["ok"] is True
    second_run_first_observation = llm.seen_messages[3][-1]["content"]
    assert "第一轮临时状态" not in second_run_first_observation


def test_loop_injects_session_context(tmp_path):
    memory = Memory(tmp_path / "memory", tmp_path / "data" / "memory.json")
    llm = FakeLLM(['{"final":"完成"}'])
    loop = AgentLoop(llm, memory, tmp_path / "workspace", max_turns=1)

    result = loop.run("继续上个任务", session_context="上一轮写入了 D:\\桌面\\讲稿.txt")

    assert result["ok"] is True
    system_prompt = llm.seen_messages[0][0]["content"]
    assert "上一轮写入了 D:\\桌面\\讲稿.txt" in system_prompt
