"""批151 红测复现腿（容器内跑）：把 HEAD 那份旧 engine 注入 sys.modules，再用最终版尺子打它。

为什么要这条腿：修复已经落在盘上（bind mount＝活面），`python3 -m unittest` 再跑只会绿。
红→绿必须两头都有原始读数，⛔ 拿「推理认为它会红」当证据。
旧件来自 `git show HEAD:butler/triggers/engine.py`，落到容器 `/tmp/old_engine_151.py`。
"""
import sys
import types
import unittest

sys.path.insert(0, "/tmp/repo149")

import butler.triggers  # noqa: F401  先让包就位，再挂 engine

SRC = "/tmp/old_engine_151.py"
src = open(SRC, encoding="utf-8").read()
mod = types.ModuleType("butler.triggers.engine")
mod.__file__ = SRC
sys.modules["butler.triggers.engine"] = mod
exec(compile(src, SRC, "exec"), mod.__dict__)

print("loaded_old_engine bytes=%d has_reset=%s has_inner=%s"
      % (len(src), "current_trace_id.reset(token)" in src, "_handle_event_inner" in src))
assert "current_trace_id.reset(token)" not in src, "这份不是修复前的旧件，红测复现作废"

import tests.test_trigger_trace_reset_1010 as ruler  # noqa: E402

suite = unittest.defaultTestLoader.loadTestsFromModule(ruler)
res = unittest.TextTestRunner(verbosity=2).run(suite)
print("VERDICT ran=%d failures=%d errors=%d skipped=%d"
      % (res.testsRun, len(res.failures), len(res.errors), len(res.skipped)))
print("red_cells=" + ",".join(sorted({m.id().split(".")[-1] for m, _ in res.failures})))
