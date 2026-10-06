#!/usr/bin/env python
"""启动冒烟检查：import butler.app，验证所有模块可加载。

用法：python scripts/smoke_import.py
退出码 0 = 通过，非 0 = 失败。
"""
import sys
import traceback

print("=== 豆包管家启动冒烟检查 ===")

# 1. 核心模块导入
modules = [
    "butler.config",
    "butler.tts.manager",
    "butler.tts.edge_tts",
    "butler.tts.nowvoice_tts",
    "butler.integrations.ha",
    "butler.integrations.llm",
    "butler.core.agent",
    "butler.core.tools",
    "butler.core.dialog",
    "butler.api.memory_routes",
    "butler.api.vibe_routes",
    "butler.skills.engines.llm_decide.engine",
    "butler.skills.engines.llm_decide.ask",
]

failed = []
for mod in modules:
    try:
        __import__(mod)
        print(f"  ✅ {mod}")
    except Exception as e:
        print(f"  ❌ {mod}: {e}")
        failed.append(mod)

# 2. TTSManager 有 speak 方法
try:
    from butler.tts.manager import TTSManager
    assert hasattr(TTSManager, "speak"), "TTSManager 缺少 speak() 方法"
    print("  ✅ TTSManager.speak() 存在")
except Exception as e:
    print(f"  ❌ TTSManager.speak(): {e}")
    failed.append("tts_speak")

# 3. MA token 不硬编码
try:
    import butler.core.agent as agent_mod
    import inspect
    src = inspect.getsource(agent_mod)
    assert "btl_xmon" not in src, "agent.py 仍有硬编码 MA token"
    print("  ✅ agent.py 无硬编码 MA token")
except Exception as e:
    print(f"  ❌ MA token 检查: {e}")
    failed.append("ma_token")

if failed:
    print(f"\n❌ 冒烟检查失败: {len(failed)} 项")
    for f in failed:
        print(f"  - {f}")
    sys.exit(1)
else:
    print(f"\n✅ 全部通过 ({len(modules)} 模块 + 2 项检查)")
    sys.exit(0)
