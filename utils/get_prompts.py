import json
import os, time
from configs.config import PROJECT_ROOT


TODO_POLICY_PROMPT = """
## TODO workflow
- For any non-trivial task, use `todo_write` early to break the goal into concrete, ordered steps.
- Keep TODO lists short and actionable, usually no more than 12 items.
- Update TODO status as work progresses. Before a final answer, complete or clear finished TODOs, or explain the remaining blocker.
"""

SKILL_POLICY_PROMPT = """
## Skill workflow
- For specialized, unfamiliar, or repeatable tasks, call `skill_discover` early with a concise task query.
- If `skill_discover` returns a plausible match, call `skill_view` before applying that skill's instructions.
- If no installed skill fits and a reusable workflow would help, you may create one with `skill_create` or install a local skill package with `skill_install`.
- `skill_install` is for local directories or SKILL.md files only. Do not install unrelated skills, and do not block simple tasks just because no skill exists.
"""


def get_system_prompt(include_memory: bool = True):
    prompt = _load_base_system_prompt()
    prompt += "\n" + TODO_POLICY_PROMPT.strip() + "\n"
    prompt += "\n" + SKILL_POLICY_PROMPT.strip() + "\n"
    prompt += f"\nToday: {time.strftime('%Y-%m-%d %a')}\n"
    if include_memory:
        prompt += get_global_memory()
    return prompt


def _load_base_system_prompt() -> str:
    settings_path = os.path.join(PROJECT_ROOT, "data", "desktop_settings.json")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("enabled", False):
            system_prompt = str(data.get("system_prompt") or "").strip()
            if system_prompt:
                return system_prompt
    except (OSError, json.JSONDecodeError):
        pass
    with open(os.path.join(PROJECT_ROOT, 'assets/system_prompt.txt'), 'r', encoding='utf-8') as f:
        return f.read()


def get_global_memory():
    prompt = "\n"
    try:
        with open(os.path.join(PROJECT_ROOT, 'memory/global_mem_insight.txt'), 'r', encoding='utf-8',
                  errors='replace') as f:
            insight = f.read()

        with open(os.path.join(PROJECT_ROOT, f'assets/insight_fixed_structure.txt'), 'r',
                  encoding='utf-8') as f:
            structure = f.read()

        prompt += f'cwd = {os.path.join(PROJECT_ROOT, "workspace")} (./)\n'
        prompt += f"\n[Memory] (../memory)\n"
        prompt += structure + '\n../memory/global_mem_insight.txt:\n'
        prompt += insight + "\n"
    except FileNotFoundError:
        pass
    return prompt
