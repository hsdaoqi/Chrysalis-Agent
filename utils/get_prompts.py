import os, time
from configs.config import PROJECT_ROOT


def get_system_prompt():
    with open(os.path.join(PROJECT_ROOT, 'assets/system_prompt.txt'), 'r', encoding='utf-8') as f: prompt = f.read()
    prompt += f"\nToday: {time.strftime('%Y-%m-%d %a')}\n"
    prompt += get_global_memory()
    return prompt


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
