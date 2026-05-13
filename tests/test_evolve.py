import json

import pytest

from chrysalis.evolve import (
    LLMSkillGenerator,
    SkillDraft,
    build_skill_candidate,
    build_skill_generation_prompt,
    crystallize_skill,
    render_replay_skill,
    safe_skill_name,
    verify_skill_candidate,
    verify_skill_code,
    verify_skill_static,
)


class FakeGenerator:
    def generate(self, candidate):
        return SkillDraft(
            code="""
def execute(task: str) -> dict:
    return {"ok": True, "message": "fake skill", "task": task}
""",
            description="fake generated skill",
            generator="fake",
        )


class FakeChatResult:
    def __init__(self, text):
        self.text = text


class FakeLLM:
    def __init__(self, text):
        self.text = text
        self.messages = None

    def chat(self, messages):
        self.messages = messages
        return FakeChatResult(self.text)


def test_build_skill_candidate_extracts_tool_steps():
    transcript = [
        {"turn": 1, "assistant": {"tool": "file_list", "args": {"path": "."}}},
        {"turn": 1, "tool": "file_list", "observation": {"ok": True}},
        {"turn": 2, "assistant": {"final": "完成"}},
    ]

    candidate = build_skill_candidate("检查 workspace", transcript)

    assert candidate.name == safe_skill_name("检查 workspace")
    assert candidate.steps_count == 1
    assert candidate.steps[0].tool == "file_list"
    assert candidate.steps[0].args == {"path": "."}


def test_verify_skill_candidate_rejects_empty_steps():
    candidate = build_skill_candidate("没有工具", [])

    with pytest.raises(ValueError, match="没有可沉淀"):
        verify_skill_candidate(candidate)


def test_rendered_skill_code_compiles():
    candidate = build_skill_candidate(
        "检查 workspace",
        [{"assistant": {"tool": "file_list", "args": {"path": "."}}}],
    )

    verify_skill_candidate(candidate)
    verify_skill_code(render_replay_skill(candidate))


def test_crystallize_skill_writes_importable_skill(tmp_path):
    transcript = [{"assistant": {"tool": "file_list", "args": {"path": "."}}}]

    result = crystallize_skill(tmp_path, "检查 workspace", transcript)

    assert result.name == safe_skill_name("检查 workspace")
    assert result.path.exists()
    assert result.steps_count == 1
    text = result.path.read_text(encoding="utf-8")
    assert "def execute" in text
    assert "STEPS" in text


def test_crystallize_skill_accepts_injected_generator(tmp_path):
    transcript = [{"assistant": {"tool": "file_list", "args": {"path": "."}}}]

    result = crystallize_skill(tmp_path, "检查 workspace", transcript, generator=FakeGenerator())

    assert result.generator == "fake"
    assert result.description == "fake generated skill"
    assert "fake skill" in result.path.read_text(encoding="utf-8")


def test_llm_skill_generator_parses_json_code():
    code = "def execute(task: str) -> dict:\\n    return {'ok': True, 'task': task}\\n"
    llm = FakeLLM(json.dumps({"description": "LLM 技能", "code": code}, ensure_ascii=False))
    candidate = build_skill_candidate(
        "检查 workspace",
        [{"assistant": {"tool": "file_list", "args": {"path": "."}}}],
    )

    draft = LLMSkillGenerator(llm).generate(candidate)

    assert draft.generator == "llm"
    assert draft.description == "LLM 技能"
    assert "def execute" in draft.code
    assert "历史工具步骤" in llm.messages[1]["content"]


def test_build_skill_generation_prompt_contains_contract():
    candidate = build_skill_candidate(
        "检查 workspace",
        [{"assistant": {"tool": "file_list", "args": {"path": "."}}}],
    )

    prompt = build_skill_generation_prompt(candidate)

    assert "只返回 JSON" in prompt
    assert "execute(task: str)" in prompt
    assert "file_list" in prompt


def test_verify_skill_static_rejects_dangerous_imports():
    code = """
import subprocess

def execute(task: str) -> dict:
    return {"ok": True}
"""

    with pytest.raises(ValueError, match="禁止导入"):
        verify_skill_static(code)


def test_verify_skill_static_requires_execute():
    with pytest.raises(ValueError, match="缺少 execute"):
        verify_skill_static("ANSWER = 1\n")


def test_crystallize_skill_does_not_write_invalid_generated_code(tmp_path):
    class BadGenerator:
        def generate(self, candidate):
            return SkillDraft(
                code="import subprocess\n\ndef execute(task: str) -> dict:\n    return {'ok': True}\n",
                description="bad",
                generator="bad",
            )

    transcript = [{"assistant": {"tool": "file_list", "args": {"path": "."}}}]

    with pytest.raises(ValueError, match="禁止导入"):
        crystallize_skill(tmp_path, "危险技能", transcript, generator=BadGenerator())
    assert list(tmp_path.glob("*.py")) == []
