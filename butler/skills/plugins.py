"""引擎注册表：内置引擎（butler/skills/engines/）与用户插件（data/plugins/）同协议加载。

目录约定：
  <root>/<engine_id>/manifest.json   {"id","name","version","entry","class"}
  <root>/<engine_id>/<entry>         Python 模块，须暴露 manifest.class 指定的类

协议（引擎类须实现）：
    async def run(self, ctx) -> SkillResult
`_` 前缀目录跳过（模板目录）。加载失败不影响其他引擎。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from butler.logging_setup import get_logger
from butler.skills.runner_types import SkillResult  # noqa: F401  (协议引用)

logger = get_logger("butler.skills.plugins")

_BUILTIN_DIR = Path(__file__).parent / "engines"


def _load_dir(root: Path, registry: dict[str, object], tag: str,
              origins: dict[str, str] | None = None) -> None:
    if not root.exists():
        return
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        manifest_path = d / "manifest.json"
        if not manifest_path.exists():
            logger.warning("%s engine %s missing manifest.json, skipped", tag, d.name)
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            eid = manifest["id"]
            entry = manifest.get("entry", "engine.py")
            cls_name = manifest.get("class", "Engine")
            spec = importlib.util.spec_from_file_location(f"butler_skills_{tag}_{eid}", d / entry)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls = getattr(module, cls_name)
            registry[eid] = cls()
            if origins is not None:
                origins[eid] = tag
            logger.info("%s engine loaded: %s (%s)", tag, eid, manifest.get("name", ""))
        except Exception as e:
            logger.warning("%s engine %s load failed: %s", tag, d.name, e)


class EngineRegistry:
    def __init__(self, user_plugins_dir: str = ""):
        self._engines: dict[str, object] = {}
        self._origins: dict[str, str] = {}   # engine_id -> builtin | user
        self._user_dir = Path(user_plugins_dir) if user_plugins_dir else None

    def load(self) -> int:
        _load_dir(_BUILTIN_DIR, self._engines, "builtin", self._origins)
        if self._user_dir:
            _load_dir(self._user_dir, self._engines, "user", self._origins)
        return len(self._engines)

    def get(self, engine_id: str):
        return self._engines.get(engine_id)

    def describe(self) -> list[dict]:
        out = []
        for eid, eng in sorted(self._engines.items()):
            item = {"id": eid, "builtin": self._origins.get(eid, "builtin") == "builtin"}
            desc = getattr(eng, "describe", None)
            if callable(desc):
                try:
                    item.update(desc() or {})
                except Exception:
                    pass
            out.append(item)
        return out
