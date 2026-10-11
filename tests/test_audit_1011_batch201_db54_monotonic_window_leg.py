"""批201 DB54 门：技能隔离计数窗口被 NTP 阶跃清空（fail-open）
（第三期审计报告 第二十轮 · DB54 medium）。

病根（`butler/skills/quarantine.py` 原 :99-122）：
    now = time.time()                      # 墙上时钟
    self._breaker_trips[skill_id].append(now)
    while ... and now - dq[0] > 3600:      # 1h 窗口用墙钟度量
        dq.popleft()                       # ← 阶跃前跳 ⇒ 旧记录被当场清掉
    if len(dq) >= 3: self.quarantine(...)

窗口是**进程内 elapsed**，却用**墙上时钟**度量 ⇒ NTP 阶跃直接篡改 `now - dq[0]`。

对照实测（报告第二十轮，可控时钟替换模块内 `time`）：
    | 场景                                      | 原仓库                | 补丁    |
    | 基准：无跳变，熔断 3 次                    | quarantined           | quarantined |
    | 基准：无跳变，超时 2 次                    | quarantined           | quarantined |
    | **向前 +2h，第 3 次熔断（达阈值）**        | **enabled ❌ fail-open** | quarantined ✅ |
    | **向前 +25h，第 2 次超时（达阈值）**       | **enabled ❌ fail-open** | quarantined ✅ |
    | 向后 −2h×2（真实跨 4h），3 次熔断          | quarantined ⚠️误伤    | quarantined ✅ |

两个方向都错，且性质相反：向前跳 ⇒ 阈值永远达不到 ⇒ **危险技能不被隔离**；
向后跳 ⇒ 陈旧记录永不淘汰 ⇒ 本该落在窗外的记录被算进窗口 ⇒ 误伤。
前者是 fail-open（安全门永不触发），比 DB52 的「窗口被拉长」更值得修。

与 DB52 的区别（同族不同层）：DB52 是 dialog 回声抑制**窗口被拉长/失效**；
DB54 是**计数器本身被清空**。

口径：`record_breaker_trip` / `record_timeout` / `quarantine` / `auto_recover_check`
全部改 `time.monotonic()`。先例是 `butler/triggers/engine.py:171/242`（同形窗口，
注释「进程内窗口：墙钟前跳会把它当场重开」）与 `butler/skills/runner.py:60`
（`_breaker_check` 60s 窗口）——`quarantine.py` 是 `butler/skills/` 里**唯一**用
`time.time()` 做进程内窗口的模块，是异类。

⛔ 两处各写各的：**审计日志**里的 `time.strftime`（`_log_audit`）与重建里的
`time.time()`（`_rebuild_quarantined_at` 算墙钟域 elapsed）**必须保留**——前者是给
人看的墙钟，后者是刻意的跨基准换算，都不是窗口度量。

「修 A 引入 B」护栏（报告第二十轮 §二 点名的同款坑）：`_quarantined_at` 改单调后，
`_rebuild_quarantined_at` 仍按盘上的 ISO **墙钟**串回填 ⇒ 两基准混用会让 24h 比较
完全失真（刚隔离的被算成「已隔离 N 年」立即恢复，或反之永不恢复）。
现已改成 elapsed 换算：`mono_now - max(0.0, wall_now - ts_wall)`。

⛔ 假时钟的建模纪律（本档最该记住的一条）：NTP 阶跃**只动墙钟**，`time.monotonic()`
只随**真实流逝**走。所以 `FakeTime` 把两者拆开——`step()` 只挪墙钟（模拟 NTP），
`elapse()` 两个都挪（模拟真实时间）。若把「真实过了 2 小时」和「前跳 2 小时」
混成一个操作，单调钟的实现会在前跳后**正确地**淘汰那条记录（它真的已经是 2 小时前
了），于是新旧两版给出同一个结果，判据腿当场变成摆设。

如实说明：时钟阶跃是**模拟的**（替换模块内 `time`），不是真 NTP 事件。
真实阶跃幅度通常远小于 2h，但「用墙上时钟度量进程内 elapsed」这个**语义错误
与幅度无关**——只要阶跃超过窗口就成立。
"""
from __future__ import annotations

import ast
import pathlib
import tempfile
import time as _real_time
import unittest
from datetime import datetime, timedelta
from unittest import mock

from butler.skills import quarantine as q

ROOT = pathlib.Path(__file__).resolve().parents[1]
QUARANTINE_PY = ROOT / "butler" / "skills" / "quarantine.py"

# DB54 管辖的窗口函数——只许单调钟。⛔ 拿文件级判据（会连坐审计日志与重建腿）。
WINDOW_FUNCS = ("record_breaker_trip", "record_timeout", "quarantine",
                "auto_recover_check")

# 假单调钟起点。⛔ 取 0 附近的小值：重建换算要减去「距今天零点」的 elapsed（可达数小时），
# 小起点会把 _quarantined_at 顶成负数，把 bug 藏成另一条假红。
FAKE_MONO_START = 1_000_000_000.0


class _FakeStore:
    """内存 store 桩：`quarantine()` 置位，`set_status()` 复位。"""

    def __init__(self):
        self._rows = {"t1": {"id": "t1", "name": "测试技能", "status": "enabled",
                             "enabled": True}}

    def get(self, skill_id):
        return self._rows.get(skill_id)

    def list(self, source="", status=""):
        rows = list(self._rows.values())
        if status:
            rows = [s for s in rows if s.get("status") == status]
        return rows

    def save(self, skill):
        self._rows[skill["id"]] = skill

    def set_status(self, skill_id, status):
        s = self._rows.get(skill_id)
        if s is None:
            return None
        s["status"] = status
        s["enabled"] = status == "enabled"
        return s

    def quarantine(self, skill_id, reason=""):
        s = self._rows.get(skill_id)
        if s is None:
            return None
        s["status"] = "quarantined"
        s["enabled"] = False
        s["quarantine_reason"] = reason
        # ISO 墙钟串，秒级精度——模拟 store.py 的 time.strftime 输出
        s["quarantined_at"] = "2026-10-11T00:00:00"
        return s


class FakeTime:
    """可控时钟：墙钟与单调钟**分开**，各挪各的。

    - `step(delta)`：只挪**墙钟**——模拟 NTP 阶跃。`monotonic()` 不受影响，
      因为真实时间没有流逝。
    - `elapse(delta)`：两个都挪——模拟真实时间流逝。
    - `time()` 转发真墙钟 + 偏移：重建换算要在墙钟域算 elapsed，这条腿本该读墙钟。

    替换的是**模块内** `q.time`——`quarantine.py` 里所有 `time.xxx` 都读这个对象。
    """

    def __init__(self, start: float = FAKE_MONO_START):
        self._mono = start
        self._wall_offset = 0.0

    def step(self, delta: float) -> None:
        self._wall_offset += delta

    def elapse(self, delta: float) -> None:
        self._mono += delta
        self._wall_offset += delta

    def monotonic(self) -> float:
        return self._mono

    def time(self) -> float:
        return _real_time.time() + self._wall_offset

    def strftime(self, fmt, *args):
        return _real_time.strftime(fmt, *args)


def _is_time_time_call(node) -> bool:
    """`time.time()` 精确形状——⛔ `time.monotonic()`（那正是我们要的）。"""
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "time"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "time")


def _time_calls_in(src: str) -> dict[str, list[str]]:
    """按 AST 归属：函数名 → 该函数里所有 `time.xxx()` 调用的 unparse 文本。

    ⛔ 文件级判据（会连坐 `_log_audit` 的 strftime 与 `_rebuild_quarantined_at`
    刻意的跨基准换算）。
    """
    hits: dict[str, list[str]] = {}
    tree = ast.parse(src)
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        calls = [ast.unparse(node) for node in ast.walk(fn)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and isinstance(node.func.value, ast.Name)
                 and node.func.value.id == "time"]
        if calls:
            hits[fn.name] = calls
    return hits


def _wall_clock_functions(src: str) -> dict[str, str]:
    """函数名 → 其中的 `time.time()` 调用（剔除 monotonic/strftime）。"""
    out = {}
    for fn, calls in _time_calls_in(src).items():
        wall = [c for c in calls if _is_time_time_call(ast.parse(c).body[0].value)]
        if wall:
            out[fn] = "; ".join(wall)
    return out


class _FakeClockCase(unittest.TestCase):
    """基类：整个用例期间保持 `q.time` 被替换（⛔ 只在构造时替换——
    那样 record_* 仍读真时钟，判据腿就成了摆设）。"""

    def setUp(self):
        self.fake = FakeTime()
        self._patcher = mock.patch.object(q, "time", self.fake)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        self.store = _FakeStore()
        self.mgr = q.QuarantineManager(self.store, tempfile.mkdtemp(prefix="q54_"))

    def status(self, sid: str = "t1") -> str:
        return self.mgr.store.get(sid)["status"]


class TestBatch201Db54Baseline(_FakeClockCase):
    """基准：无阶跃，两版行为一致（补丁没改正常路径）。"""

    def test_01_breaker_three_trips_quarantines(self):
        for _ in range(3):
            self.fake.elapse(0.001)   # 真实间隔毫秒级
            self.mgr.record_breaker_trip("t1")
        self.assertEqual(self.status(), "quarantined", "无阶跃下 3 次熔断本该隔离")
        self.assertIn("t1", self.mgr._quarantined_at)

    def test_02_timeout_two_hits_quarantines(self):
        for _ in range(2):
            self.fake.elapse(0.001)
            self.mgr.record_timeout("t1", 31000)
        self.assertEqual(self.status(), "quarantined", "无阶跃下 2 次超时本该隔离")


class TestBatch201Db54ForwardStep(_FakeClockCase):
    """向前阶跃：原仓库 fail-open（阈值永远达不到），补丁不被清空。

    这一步是 DB54 的**主病灶**——两个方向里危害更重的那一个
    （安全门永不触发，危险技能照跑）。三次熔断真实间隔毫秒级、阈值本该达到。
    """

    def test_03_forward_2h_then_third_trip_still_quarantines(self):
        """报告第二十轮实测表第三行：原仓库 enabled ❌ / 补丁 quarantined ✅。"""
        self.mgr.record_breaker_trip("t1")
        self.fake.step(2 * 3600)                  # NTP 前跳 2 小时（真实没流逝）
        self.mgr.record_breaker_trip("t1")
        self.mgr.record_breaker_trip("t1")
        self.assertEqual(self.status(), "quarantined",
                         "前跳 2h 后计数器被清空⇒危险技能永不隔离（fail-open）")
        self.assertEqual(len(self.mgr._breaker_trips["t1"]), 0, "触发后窗口已清空")

    def test_04_forward_25h_then_second_timeout_still_quarantines(self):
        """报告第二十轮实测表第四行：原仓库 enabled ❌ / 补丁 quarantined ✅。"""
        self.mgr.record_timeout("t1", 31000)
        self.fake.step(25 * 3600)                 # 25h > 24h 窗口
        self.mgr.record_timeout("t1", 31000)
        self.assertEqual(self.status(), "quarantined",
                         "前跳 25h 后超时计数被清空⇒永不隔离（fail-open）")


class TestBatch201Db54BackwardStep(_FakeClockCase):
    """向后阶跃：陈旧记录赖在窗口里 ⇒ 误伤。

    场景构造：三次熔断**真实**相隔 2 小时（本就该落在 1h 窗外、不该计入），
    但每次流逝后墙钟向后回跳 4h ⇒ 墙钟差被压到负值 ⇒ 旧记录永不淘汰 ⇒ 虚增到 3 次。
    单调钟按真实 elapsed 计 ⇒ 正确淘汰 ⇒ 不算数。
    """

    def test_05_backward_step_hides_stale_trips(self):
        self.mgr.record_breaker_trip("t1")
        self.fake.elapse(2 * 3600)          # 真实流逝 2 小时
        self.fake.step(-4 * 3600)           # 墙钟回跳 4h
        self.mgr.record_breaker_trip("t1")
        self.fake.elapse(2 * 3600)
        self.fake.step(-4 * 3600)
        self.mgr.record_breaker_trip("t1")
        self.assertEqual(self.status(), "enabled",
                         "墙钟回跳让陈旧记录赖在窗口里⇒本该 1 次却算成 3 次（误伤）")
        self.assertEqual(len(self.mgr._breaker_trips["t1"]), 1,
                         "真实跨过 1h 窗口的记录不该被算进计数")


class TestBatch201Db54WindowSemantics(_FakeClockCase):
    """窗口语义本身没被改窄：正常滑动淘汰仍要工作。"""

    def test_06_old_breaker_trips_are_pruned_outside_1h(self):
        self.mgr.record_breaker_trip("t1")
        self.fake.elapse(2 * 3600)                   # 真实流逝 2 小时
        self.mgr.record_breaker_trip("t1")
        self.mgr.record_breaker_trip("t1")
        self.assertEqual(self.status(), "enabled", "真跨 2 小时的旧记录没被淘汰⇒阈值虚高")
        self.assertEqual(len(self.mgr._breaker_trips["t1"]), 2,
                         "窗口只剩窗口内的记录，⛔ 陈旧记录赖着算数")

    def test_07_auto_recover_fires_after_24h_and_only_after(self):
        self.mgr.quarantine("t1", "测试")
        self.assertEqual(self.mgr.auto_recover_check(), [], "未到 24h 不该恢复")
        self.fake.elapse(q.AUTO_RECOVER_AFTER + 1)
        recovered = self.mgr.auto_recover_check()
        self.assertEqual(recovered, ["t1"], "满 24h 后没自动恢复⇒自愈路径断了")
        self.assertEqual(self.status(), "disabled",
                         "自动恢复目标是 disabled（仍需人工启用），不是 enabled")
        self.assertNotIn("t1", self.mgr._quarantined_at, "恢复后残留时刻条目⇒会被重复恢复")

    def test_08_no_duplicate_recovery_after_restore(self):
        """恢复后 `_quarantined_at` 必须摘掉，否则下次调用又恢复一次。"""
        self.mgr.quarantine("t1", "测试")
        self.fake.elapse(q.AUTO_RECOVER_AFTER + 1)
        self.assertEqual(self.mgr.auto_recover_check(), ["t1"])
        self.assertEqual(self.mgr.auto_recover_check(), [], "已恢复的技能又被恢复一次")


class TestBatch201Db54CrossClockRebuild(unittest.TestCase):
    """「修 A 引入 B」护栏：`_quarantined_at` 单调化后，重建仍从墙钟 ISO 串来。

    两基准直接混用会让 24h 比较完全失真——刚隔离的被算成「已隔离 N 年」立即恢复，
    或反之永不恢复。
    """

    def _install(self, iso: str) -> FakeTime:
        """装好假时钟并隔离一个技能，`iso` 是盘上的 `quarantined_at`。"""
        fake = FakeTime()
        patcher = mock.patch.object(q, "time", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        store = _FakeStore()
        store.quarantine("t1", "测试")
        store.get("t1")["quarantined_at"] = iso    # 覆写成可控制的墙钟时刻
        mgr = q.QuarantineManager(store, tempfile.mkdtemp(prefix="q54_"))
        return fake, mgr

    def test_09_rebuild_converts_wall_iso_to_monotonic_domain(self):
        """盘上「1 小时前」的隔离 ⇒ 单调值 = 现在 - 3600，落在单调域内。"""
        iso = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        fake, mgr = self._install(iso)
        self.assertIn("t1", mgr._quarantined_at, "重建没捞回刚隔离的技能")
        ts = mgr._quarantined_at["t1"]
        self.assertGreater(ts, 0.0, "重建值非正⇒像是没算")
        self.assertLess(ts, fake.monotonic(), f"重建值 {ts} 落在未来⇒24h 比较立即判过界")
        self.assertAlmostEqual(fake.monotonic() - ts, 3600.0, delta=120.0,
                               msg="换算误差过大⇒两个基准直接相减了")

    def test_10_monotonic_clock_step_does_not_distort_auto_recover(self):
        """跨阶跃的 24h 判断必须按 elapsed 走，⛔ 按两个不同基准相减。"""
        iso = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        fake, mgr = self._install(iso)
        self.assertEqual(mgr.auto_recover_check(), [], "刚隔离就被恢复⇒基准混了")
        fake.elapse(q.AUTO_RECOVER_AFTER + 10)
        self.assertEqual(mgr.auto_recover_check(), ["t1"],
                         "满 24h 后没恢复⇒基准混了（另一个方向的失真）")


def _uses_dumps_blacklist_scan(fn_node):
    """形状尺：函数体里同时出现 `json.dumps` ＋ 黑名单成员判据（`in <Name>`）。

    原实现形状＝整体序列化后子串匹配，天然跨字段命中。
    """
    if fn_node is None:
        return False
    has_dumps = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "dumps" for n in ast.walk(fn_node))
    has_member = any(isinstance(n, ast.Compare)
                     and any(isinstance(r, ast.Name) for r in n.comparators)
                     for n in ast.walk(fn_node))
    return has_dumps and has_member


class TestBatch201Db53ShapeRuler(unittest.TestCase):
    """形状尺：两侧校准，⛔ 只测拦截（会漏掉尺写窄了）也⛔ 只测放行（会漏掉判太宽）。"""

    def test_11_ruler_bites_the_synthetic_old_shape(self):
        old = ('import json\n'
               'BLACKLIST = ["rm -rf"]\n'
               'def check_security(self, skill):\n'
               '    s = json.dumps(skill, ensure_ascii=False).lower()\n'
               '    for p in BLACKLIST:\n'
               '        if p in s:\n'
               '            return False, p\n'
               '    return True, ""\n')
        fn = next(n for n in ast.walk(ast.parse(old))
                  if isinstance(n, ast.FunctionDef) and n.name == "check_security")
        self.assertTrue(_uses_dumps_blacklist_scan(fn), "尺子咬不住旧形状⇒test_12 是摆设")

    def test_12_ruler_passes_the_new_scan_shape(self):
        """放行侧校准：新实现没有 `json.dumps`，尺子不该把它判成旧形状。"""
        new = ('def _walk(node):\n'
               '    if isinstance(node, dict):\n'
               '        for k, v in node.items():\n'
               '            yield from _walk(v)\n'
               '    elif isinstance(node, str):\n'
               '        yield node\n'
               'def check_security(self, skill):\n'
               '    for s in _walk(skill):\n'
               '        for p in PATTERNS:\n'
               '            if p in s:\n'
               '                return False, p\n'
               '    return True, ""\n')
        fn = next(n for n in ast.walk(ast.parse(new))
                  if isinstance(n, ast.FunctionDef) and n.name == "check_security")
        self.assertFalse(_uses_dumps_blacklist_scan(fn), "尺子判太宽⇒会逼着人保留 dumps")

    def test_13_production_check_security_is_not_the_dumps_shape(self):
        tree = ast.parse(QUARANTINE_PY.read_text(encoding="utf-8"))
        fn = None
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef) and node.name == "check_security"
                    and node.col_offset == 4):
                fn = node
        self.assertIsNotNone(fn, "找不到 QuarantineManager.check_security")
        self.assertFalse(_uses_dumps_blacklist_scan(fn),
                         "check_security 还在整体序列化扫描⇒NL 字段照样会被扫")


class TestBatch201Db54ShapeRuler(unittest.TestCase):
    """形状尺：窗口函数只许单调钟；⛔ 连坐审计日志与重建腿。"""

    def test_14_ruler_bites_synthetic_wall_clock_window(self):
        old = ('import time\n'
               'def record_breaker_trip(self, sid):\n'
               '    now = time.time()\n'
               '    dq.append(now)\n'
               '    while dq and now - dq[0] > 3600:\n'
               '        dq.popleft()\n')
        self.assertEqual(_wall_clock_functions(old).get("record_breaker_trip"), "time.time()",
                         "尺子咬不住墙钟窗口⇒test_16 是摆设")

    def test_15_ruler_passes_synthetic_monotonic_window(self):
        """放行侧校准：`time.monotonic()` 不是墙钟——判据是 `attr == "time"`，⛔ 前缀匹配。"""
        new = ('import time\n'
               'def record_breaker_trip(self, sid):\n'
               '    now = time.monotonic()\n'
               '    dq.append(now)\n')
        self.assertNotIn("record_breaker_trip", _wall_clock_functions(new),
                         "尺子把 monotonic 窗口误判成墙钟⇒判太宽")

    def test_16_window_functions_use_no_wall_clock(self):
        hits = _wall_clock_functions(QUARANTINE_PY.read_text(encoding="utf-8"))
        offenders = {f: c for f, c in hits.items() if f in WINDOW_FUNCS}
        self.assertEqual(offenders, {},
                         f"窗口函数还在用 time.time()（NTP 阶跃会清掉计数器⇒fail-open）：{offenders}")

    def test_17_rebuild_leg_is_the_only_legitimate_wall_clock_reader(self):
        """`_rebuild_quarantined_at` 的 `time.time()` 是刻意的跨基准换算，⛔ 被尺子误伤。

        这条是放行侧校准——没有它，test_16 一旦写宽（文件级判据）会把这条腿
        一起判死，逼着有人把换算删掉、重引入基准混用。
        """
        hits = _wall_clock_functions(QUARANTINE_PY.read_text(encoding="utf-8"))
        self.assertIn("_rebuild_quarantined_at", hits,
                      "重建腿丢了 wall-clock 读取⇒无法把盘上 ISO 串折回单调域")

    def test_18_audit_log_still_on_wall_clock(self):
        """审计日志时间戳是给**人**看的墙钟，⛔ 跟着窗口一起改单调。"""
        calls = _time_calls_in(QUARANTINE_PY.read_text(encoding="utf-8"))
        self.assertIn("_log_audit", calls, "_log_audit 里找不到 time.xxx⇒审计日志可能挂了")
        self.assertTrue(any("strftime" in c for c in calls["_log_audit"]),
                        "_log_audit 没再用 time.strftime⇒审计日志时间戳语义变了")


if __name__ == "__main__":
    unittest.main()
