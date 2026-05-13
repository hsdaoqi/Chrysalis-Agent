"""技能沉淀流水线。

这一版先把“候选 -> 生成 -> 验证 -> 写入”拆清楚。
生成内容仍然是保守的历史工具轨迹回放，后续再把 generate 阶段替换成 LLM 泛化。
"""

import importlib.util
import json
import py_compile
import re
import ast
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol


DISALLOWED_IMPORT_ROOTS = {"os", "subprocess", "shutil", "socket", "ctypes", "sys"}
DISALLOWED_CALL_NAMES = {"eval", "exec", "compile", "open", "input", "__import__"}
DISALLOWED_CALL_ATTRS = {
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("os", "system"),
    ("os", "popen"),
    ("shutil", "rmtree"),
}


@dataclass(frozen=True)
class SkillStep:
    tool: str
    args: dict


@dataclass(frozen=True)
class SkillCandidate:
    name: str
    description: str
    source_task: str
    steps: list[SkillStep]

    @property
    def steps_count(self) -> int:
        return len(self.steps)


@dataclass(frozen=True)
class SkillPersistResult:
    name: str
    path: Path
    description: str
    steps_count: int
    generator: str


@dataclass(frozen=True)
class SkillDraft:
    code: str
    description: str
    generator: str = "replay"


class SkillGenerator(Protocol):
    def generate(self, candidate: SkillCandidate) -> SkillDraft:
        ...


class ReplaySkillGenerator:
    def generate(self, candidate: SkillCandidate) -> SkillDraft:
        return SkillDraft(
            code=render_replay_skill(candidate),
            description=candidate.description,
            generator="replay",
        )


class LLMSkillGenerator:
    """可选的 LLM 技能生成器。

    这里先只提供接口和严格解析，不在 Kernel 中默认启用。
    生成结果仍然要经过编译和导入验证。
    """

    def __init__(self, llm):
        self.llm = llm

    def generate(self, candidate: SkillCandidate) -> SkillDraft:
        response = self.llm.chat([
            {"role": "system", "content": "你负责把成功任务轨迹泛化为 Chrysalis 技能。只返回 JSON。"},
            {"role": "user", "content": build_skill_generation_prompt(candidate)},
        ])
        data = _parse_json_object(response.text)
        code = str(data.get("code", "")).strip()
        description = str(data.get("description") or candidate.description).strip()
        if not code:
            raise ValueError("LLM 没有返回 code")
        return SkillDraft(code=code, description=description, generator="llm")


def safe_skill_name(task: str) -> str:
    words = re.findall(r"[a-zA-Z0-9]+", task.lower())
    name = "_".join(words[:5]) or "learned_skill"
    if name[0].isdigit():
        name = "skill_" + name
    return name[:60]


def build_skill_candidate(task: str, transcript: list[dict]) -> SkillCandidate:
    steps = []
    for item in transcript:
        assistant = item.get("assistant")
        if isinstance(assistant, dict) and assistant.get("tool"):
            steps.append(SkillStep(
                tool=str(assistant.get("tool")),
                args=assistant.get("args") or {},
            ))
    return SkillCandidate(
        name=safe_skill_name(task),
        description=f"来源任务：{task[:80]}",
        source_task=task,
        steps=steps,
    )


def render_replay_skill(candidate: SkillCandidate) -> str:
    steps = [{"tool": step.tool, "args": step.args} for step in candidate.steps]
    return f'''"""自动创建的技能。

来源任务：{candidate.source_task}
沉淀方式：按历史成功工具轨迹回放。
"""

from chrysalis.tools import run_tool


STEPS = {steps!r}


def execute(task: str) -> dict:
    results = []
    for step in STEPS:
        result = run_tool(step["tool"], step.get("args") or {{}})
        results.append({{"step": step, "result": result}})
        if not result.get("ok"):
            return {{"ok": False, "error": "回放步骤失败", "results": results}}
    return {{
        "ok": True,
        "message": "已按历史成功轨迹回放技能。",
        "original_task": {candidate.source_task!r},
        "steps_count": len(STEPS),
        "results": results,
    }}
'''


def build_skill_generation_prompt(candidate: SkillCandidate) -> str:
    steps = [{"tool": step.tool, "args": step.args} for step in candidate.steps]
    return (
        "请根据下面一次成功任务轨迹，生成一个可复用的 Python 技能文件。\n"
        "要求：\n"
        "1. 只返回 JSON 对象，不要 Markdown。\n"
        "2. JSON 字段必须包含 description 和 code。\n"
        "3. code 必须定义 execute(task: str) -> dict。\n"
        "4. 只能使用 chrysalis.tools 里的原子工具，失败时返回 {'ok': False, ...}。\n\n"
        f"技能名：{candidate.name}\n"
        f"来源任务：{candidate.source_task}\n"
        f"历史工具步骤：{steps!r}\n"
    )


def verify_skill_candidate(candidate: SkillCandidate) -> None:
    if not candidate.name:
        raise ValueError("技能名不能为空")
    if not candidate.steps:
        raise ValueError("没有可沉淀的工具步骤")
    for index, step in enumerate(candidate.steps, 1):
        if not step.tool:
            raise ValueError(f"第 {index} 步缺少工具名")
        if not isinstance(step.args, dict):
            raise ValueError(f"第 {index} 步参数不是 dict")


def verify_skill_code(code: str) -> None:
    verify_skill_static(code)
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "candidate_skill.py"
        path.write_text(code, encoding="utf-8")
        py_compile.compile(str(path), doraise=True)
        verify_skill_file(path)


def verify_skill_static(code: str) -> None:
    tree = ast.parse(code)
    _verify_execute_function(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in DISALLOWED_IMPORT_ROOTS:
                    raise ValueError(f"技能代码禁止导入 {root}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            if root in DISALLOWED_IMPORT_ROOTS:
                raise ValueError(f"技能代码禁止导入 {root}")
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if isinstance(name, str) and name in DISALLOWED_CALL_NAMES:
                raise ValueError(f"技能代码禁止调用 {name}")
            if isinstance(name, tuple) and name in DISALLOWED_CALL_ATTRS:
                raise ValueError(f"技能代码禁止调用 {'.'.join(name)}")


def verify_skill_file(path: Path) -> None:
    spec = importlib.util.spec_from_file_location(f"chrysalis_verify_skill_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"无法加载技能文件：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    execute = getattr(module, "execute", None)
    if not callable(execute):
        raise ValueError("技能文件缺少 execute(task)")


def persist_skill(skills_dir: Path, candidate: SkillCandidate, draft: SkillDraft) -> SkillPersistResult:
    skills_dir.mkdir(parents=True, exist_ok=True)
    path = _next_skill_path(skills_dir, candidate.name)
    verify_skill_code(draft.code)
    path.write_text(draft.code, encoding="utf-8")
    verify_skill_file(path)
    return SkillPersistResult(
        name=path.stem,
        path=path,
        description=draft.description,
        steps_count=candidate.steps_count,
        generator=draft.generator,
    )


def crystallize_skill(
    skills_dir: Path,
    task: str,
    transcript: list[dict],
    generator: SkillGenerator | None = None,
) -> SkillPersistResult:
    candidate = build_skill_candidate(task, transcript)
    verify_skill_candidate(candidate)
    draft = (generator or ReplaySkillGenerator()).generate(candidate)
    return persist_skill(skills_dir, candidate, draft)


def write_skill(skills_dir: Path, task: str, transcript: list[dict]) -> tuple[str, Path]:
    """兼容旧接口。新代码优先使用 crystallize_skill。"""
    result = crystallize_skill(skills_dir, task, transcript)
    return result.name, result.path


def _next_skill_path(skills_dir: Path, name: str) -> Path:
    path = skills_dir / f"{name}.py"
    counter = 2
    while path.exists():
        path = skills_dir / f"{name}_{counter}.py"
        counter += 1
    return path


def _parse_json_object(text: str) -> dict:
    try:
        value = json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("LLM 返回内容不是 JSON 对象")
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM 返回内容不是 JSON 对象")
    return value


def _verify_execute_function(tree: ast.AST) -> None:
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.FunctionDef) and node.name == "execute":
            if len(node.args.args) != 1:
                raise ValueError("execute 必须只接收 task 一个参数")
            return
    raise ValueError("技能代码缺少 execute(task)")


def _call_name(node: ast.AST) -> str | tuple[str, str] | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return (node.value.id, node.attr)
    return None
