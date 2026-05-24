import json
import os, time
from configs.config import PROJECT_ROOT


def get_system_prompt(include_memory: bool = True):
    prompt = _load_base_system_prompt()
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
