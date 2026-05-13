import json
from dataclasses import dataclass

from chrysalis.config import AgentConfig
from chrysalis.evolve import safe_skill_name
from chrysalis.kernel import Kernel, format_interactive_result, run_interactive


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


class FakeKernel:
    def __init__(self):
        self.tasks = []

    def run(self, task):
        self.tasks.append(task)
        return {"ok": True, "final": f"完成：{task}"}


class FakeSkillGenerator:
    def generate(self, candidate):
        from chrysalis.evolve import SkillDraft

        return SkillDraft(
            code="def execute(task: str) -> dict:\n    return {'ok': True}\n",
            description="fake skill",
            generator="fake",
        )


class BadSkillGenerator:
    def generate(self, candidate):
        from chrysalis.evolve import SkillDraft

        return SkillDraft(
            code="import subprocess\n\ndef execute(task: str) -> dict:\n    return {'ok': True}\n",
            description="bad skill",
            generator="bad",
        )


def make_config(tmp_path, **kwargs):
    values = {
        "data_dir": tmp_path / "data",
        "memory_dir": tmp_path / "memory",
        "skills_dir": tmp_path / "skills",
        "workspace_dir": tmp_path / "workspace",
        "max_turns": 24,
        "min_skill_turns": 16,
    }
    values.update(kwargs)
    return AgentConfig(**values)


def test_kernel_lists_workspace_without_llm(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("hello", encoding="utf-8")
    config = make_config(tmp_path, workspace_dir=workspace)

    kernel = Kernel(config=config, llm=None)
    result = kernel.run("列出 workspace 里的文件")

    assert result["ok"] is True
    names = {item["name"] for item in result["data"]["entries"]}
    assert "a.txt" in names
    lines = config.trace_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["task"] == "列出 workspace 里的文件"
    assert record["tool_calls"] == 0


def test_kernel_reports_direct_progress(tmp_path):
    events = []
    config = make_config(tmp_path)

    result = Kernel(config=config, llm=None, progress=events.append).run("本项目有哪些目录")

    assert result["ok"] is True
    assert events[0] == "开始任务：本项目有哪些目录"
    assert any("本地直达" in item for item in events)


def test_kernel_lists_project_dirs_without_llm(tmp_path):
    config = make_config(tmp_path)

    result = Kernel(config=config, llm=None).run("本项目有哪些目录")

    assert result["ok"] is True
    names = {item["name"] for item in result["data"]["directories"]}
    assert "chrysalis" in names
    assert "memory" in names
    assert "workspace" in names


def test_short_success_does_not_write_skill(tmp_path):
    config = make_config(tmp_path)
    llm = FakeLLM([
        '{"tool":"file_list","args":{"path":"."}}',
        '{"final":"完成"}',
    ])

    result = Kernel(config=config, llm=llm).run("看看目录")

    assert result["ok"] is True
    assert "skill" not in result
    assert list(config.skills_dir.glob("*.py")) == []


def test_long_tool_chain_writes_skill(tmp_path):
    config = make_config(tmp_path)
    responses = ['{"tool":"file_list","args":{"path":"."}}' for _ in range(15)]
    responses.append('{"final":"完成"}')

    result = Kernel(config=config, llm=FakeLLM(responses)).run("反复检查 workspace 状态并总结")

    assert result["ok"] is True
    assert "skill" in result
    assert result["skill_steps"] == 15
    assert result["skill_generator"] == "replay"
    assert (config.skills_dir / f"{safe_skill_name('反复检查 workspace 状态并总结')}.py").exists()
    record = json.loads(config.trace_log.read_text(encoding="utf-8").splitlines()[-1])
    assert record["turns"] == 16
    assert record["tool_calls"] == 15
    assert record["wrote_skill"] is True


def test_kernel_uses_injected_skill_generator(tmp_path):
    config = make_config(tmp_path)
    responses = ['{"tool":"file_list","args":{"path":"."}}' for _ in range(15)]
    responses.append('{"final":"完成"}')

    result = Kernel(config=config, llm=FakeLLM(responses), skill_generator=FakeSkillGenerator()).run("生成 fake 技能")

    assert result["ok"] is True
    assert result["skill_generator"] == "fake"
    assert "fake skill" in config.memory_json.read_text(encoding="utf-8") or "fake skill" in (config.memory_dir / "global_mem_insight.txt").read_text(encoding="utf-8")


def test_kernel_skips_l1_when_skill_validation_fails(tmp_path):
    config = make_config(tmp_path)
    responses = ['{"tool":"file_list","args":{"path":"."}}' for _ in range(15)]
    responses.append('{"final":"完成"}')

    result = Kernel(config=config, llm=FakeLLM(responses), skill_generator=BadSkillGenerator()).run("生成坏技能")

    assert result["ok"] is True
    assert "skill" not in result
    assert any("技能沉淀验证失败" in item for item in result["warnings"])
    assert list(config.skills_dir.glob("*.py")) == []
    assert "bad skill" not in (config.memory_dir / "global_mem_insight.txt").read_text(encoding="utf-8")


def test_kernel_carries_session_context_between_runs(tmp_path):
    config = make_config(tmp_path)
    llm = FakeLLM([
        '{"final":"已写入 D:\\\\桌面\\\\讲稿.txt"}',
        '{"final":"已根据上一轮文件继续扩写"}',
    ])
    kernel = Kernel(config=config, llm=llm)

    kernel.run("写一份讲稿")
    result = kernel.run("字数太少了")

    assert result["ok"] is True
    second_system_prompt = llm.seen_messages[1][0]["content"]
    assert "本次交互会话的最近上下文" in second_system_prompt
    assert "D:\\桌面\\讲稿.txt" in second_system_prompt


def test_using_existing_skill_blocks_new_skill(tmp_path):
    config = make_config(tmp_path)
    (config.skills_dir / "check_workspace.py").write_text(
        """
def execute(task: str) -> dict:
    return {"ok": True, "message": "已有技能完成"}
""",
        encoding="utf-8",
    )
    config.memory_dir.mkdir(parents=True, exist_ok=True)
    responses = ['{"skill":"check_workspace"}']
    responses.extend('{"tool":"file_list","args":{"path":"."}}' for _ in range(15))
    responses.append('{"final":"完成"}')

    result = Kernel(config=config, llm=FakeLLM(responses)).run("检查 workspace 并总结")

    assert result["ok"] is True
    assert "skill" not in result
    assert len(list(config.skills_dir.glob("*.py"))) == 1


def test_existing_skill_name_blocks_duplicate(tmp_path):
    config = make_config(tmp_path)
    task = "重复的长流程"
    (config.skills_dir / f"{safe_skill_name(task)}.py").write_text(
        "def execute(task: str) -> dict:\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    responses = ['{"tool":"file_list","args":{"path":"."}}' for _ in range(15)]
    responses.append('{"final":"完成"}')

    result = Kernel(config=config, llm=FakeLLM(responses)).run(task)

    assert result["ok"] is True
    assert "skill" not in result
    assert len(list(config.skills_dir.glob("*.py"))) == 1


def test_interactive_mode_runs_until_exit():
    kernel = FakeKernel()
    inputs = iter(["第一个任务", "", "第二个任务", "/exit"])
    outputs = []

    run_interactive(kernel, input_func=lambda prompt: next(inputs), output_func=outputs.append)

    assert kernel.tasks == ["第一个任务", "第二个任务"]
    assert outputs[0].startswith("Chrysalis 交互模式")
    assert "完成：第一个任务" in outputs
    assert outputs[-1] == "已退出。"


def test_format_interactive_result_prefers_error_when_no_final():
    result = format_interactive_result({"ok": False, "error": "没有配置 API Key"})

    assert result == "出错：没有配置 API Key"
