"""技能执行。

这一层对应 GA 的“记忆里有 skill，下次直接调用”。
L1 记忆会注入给模型，由模型决定是否调用技能；这里只负责导入
skills/<name>.py 并调用 execute(task)。
"""

import importlib.util
from pathlib import Path

from chrysalis.memory import Memory


class SkillLibrary:
    def __init__(self, skills_dir: Path, memory: Memory):
        self.skills_dir = skills_dir
        self.memory = memory

    def execute(self, name: str, task: str) -> dict:
        """导入并执行一个技能文件。"""
        path = self.skills_dir / f"{name}.py"
        if not path.exists():
            return {"ok": False, "error": f"技能不存在：{name}"}

        spec = importlib.util.spec_from_file_location(f"chrysalis_user_skill_{name}", path)
        if spec is None or spec.loader is None:
            return {"ok": False, "error": f"无法加载技能：{name}"}

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            if not hasattr(module, "execute"):
                return {"ok": False, "error": f"技能缺少 execute(task)：{name}"}
            result = module.execute(task)
            if not isinstance(result, dict):
                return {"ok": False, "error": f"技能返回值不是 dict：{name}"}
            result.setdefault("ok", True)
            return result
        except Exception as exc:
            return {"ok": False, "error": f"技能执行失败：{exc}"}
