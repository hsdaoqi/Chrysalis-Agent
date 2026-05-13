"""自动创建的技能。

来源任务：你的技能，skills沉淀的触发代码在哪
沉淀方式：按历史成功工具轨迹回放。
"""

from chrysalis.tools import run_tool


STEPS = [{'tool': 'file_list', 'args': {'path': '.'}}, {'tool': 'file_list', 'args': {'path': 'D:\\Project\\Chrysalis'}}, {'tool': 'file_list', 'args': {'path': 'D:\\Project\\Chrysalis\\skills'}}, {'tool': 'file_list', 'args': {'path': 'D:\\Project\\Chrysalis\\chrysalis'}}, {'tool': 'file_read', 'args': {'path': 'D:\\Project\\Chrysalis\\chrysalis\\skills.py', 'start': 1, 'count': 200}}, {'tool': 'file_read', 'args': {'path': 'D:\\Project\\Chrysalis\\chrysalis\\evolve.py', 'start': 1, 'count': 200}}, {'tool': 'file_read', 'args': {'path': 'D:\\Project\\Chrysalis\\chrysalis\\agent_loop.py', 'start': 1, 'count': 100}}, {'tool': 'file_read', 'args': {'path': 'D:\\Project\\Chrysalis\\chrysalis\\agent_loop.py', 'start': 100, 'count': 60}}, {'tool': 'file_read', 'args': {'path': 'D:\\Project\\Chrysalis\\chrysalis\\tools.py', 'start': 1, 'count': 200}}, {'tool': 'file_read', 'args': {'path': 'D:\\Project\\Chrysalis\\chrysalis\\agent_loop.py', 'start': 1, 'count': 151}}, {'tool': 'file_read', 'args': {'path': 'D:\\Project\\Chrysalis\\chrysalis\\tools.py', 'start': 250, 'count': 100}}, {'tool': 'file_read', 'args': {'path': 'D:\\Project\\Chrysalis\\chrysalis\\tools.py', 'start': 301, 'count': 50}}, {'tool': 'file_read', 'args': {}}, {'tool': 'file_read', 'args': {'path': 'D:\\Project\\Chrysalis\\chrysalis\\tools.py', 'start': 200, 'count': 51}}, {'tool': 'file_read', 'args': {'path': 'D:\\Project\\Chrysalis\\chrysalis\\tools.py', 'start': 250, 'count': 100, 'show_linenos': True}}, {'tool': 'file_read', 'args': {'path': 'D:\\Project\\Chrysalis\\chrysalis\\tools.py', 'start': 250, 'count': 100, 'show_linenos': True}}, {'tool': 'code_run', 'args': {'code': 'import json\nwith open(r\'D:\\Project\\Chrysalis\\chrysalis\\tools.py\', encoding=\'utf-8\') as f:\n    lines = f.readlines()\n    for i, line in enumerate(lines[250:330], start=251):\n        print(f"{i}:{line.rstrip()}")', 'timeout': 5}}, {'tool': 'code_run', 'args': {'code': 'import os\nimport re\n\nroot = r\'D:\\Project\\Chrysalis\'\nkeyword = \'start_long_term_update\'\nresults = []\nfor dirpath, dirnames, filenames in os.walk(root):\n    if \'__pycache__\' in dirpath or \'.venv\' in dirpath or \'workspace\' in dirpath:\n        continue\n    for fn in filenames:\n        if fn.endswith(\'.py\') or fn.endswith(\'.md\') or fn.endswith(\'.txt\') or fn == \'pyproject.toml\':\n            fpath = os.path.join(dirpath, fn)\n            try:\n                with open(fpath, \'r\', encoding=\'utf-8\') as f:\n                    for i, line in enumerate(f, 1):\n                        if keyword in line:\n                            results.append(f"{fpath}:{i}:{line.strip()}")\n            except:\n                pass\n\nprint(json.dumps(results, indent=2, ensure_ascii=False) if results else \'NOT FOUND\')\n', 'timeout': 10}}]


def execute(task: str) -> dict:
    results = []
    for step in STEPS:
        result = run_tool(step["tool"], step.get("args") or {})
        results.append({"step": step, "result": result})
        if not result.get("ok"):
            return {"ok": False, "error": "回放步骤失败", "results": results}
    return {
        "ok": True,
        "message": "已按历史成功轨迹回放技能。",
        "original_task": '你的技能，skills沉淀的触发代码在哪',
        "steps_count": len(STEPS),
        "results": results,
    }
