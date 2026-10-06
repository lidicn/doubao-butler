"""trigger 引擎：事件匹配 + 条件校验 + 冷却 + 动作执行。

事件来源：dialog.on_event 分发（人脸/语音/按钮等），或 APScheduler 定时事件。
动作执行：调用 rt.runner.run(skill_id, source="trigger", payload=渲染后参数)。
输出由技能自身的 output 字段决定（TV/小爱/Bark），trigger 不直接发声。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime

from butler.logging_setup import get_logger
from butler.store import repo, write_failures

logger = get_logger("butler.triggers.engine")

_PARAM_RE = re.compile(r"\{\{\s*event\.(\w+)\s*\}\}")


def _only_terminal(action_results: list[dict]) -> bool:
    """本轮失败项是否全是「隔离态」这种人工终态（格3②，裁定 2026-09-30）。

    `runner.run()` 对 `status=="quarantined"` 返回 `{"ok": False, "status": "quarantined"}`，
    那是人按下去的终态、不是一次运行失败。旧写法把它计入 `_fail_count`，而该计数只在成功时归零
    ⇒ 技能一日不解除隔离就永不自愈（现网那行 `8` ＞ 阈值 `5` 就是这条的盘上证据）。
    """
    failed = [r for r in action_results if not r.get("ok")]
    return bool(failed) and all(r.get("status") == "quarantined" for r in failed)


class TriggerEngine:
    def __init__(self, store):
        self.store = store
        self.rt = None  # 由 app.py 在 runtime 就绪后注入
        # 时基分工（审计 2026-10-02 表行 62）：只有 _last_fired 跨重启落盘 ⇒ epoch 墙钟；
        # 下面三枚都是进程内窗口 ⇒ monotonic，墙钟跳动不许改变它们的语义。
        self._last_fired: dict[str, float] = {}
        # GATE 1: 并发执行锁 — 同一 trigger 正在跑就不重入
        self._running: set[str] = set()
        # GATE 2: 事件级防抖 — 同 (event,member,room) 30s 内重复事件丢弃
        self._event_seen: dict[str, float] = {}  # monotonic
        # GATE 3: 输出设备资源锁 — TV 被占用期间其他 trigger 不能用 TV
        # key=设备标识(如 "tv", "xiaoai:书房"), value=锁到期时间戳
        self._device_locks: dict[str, float] = {}  # monotonic
        self._DEVICE_LOCK_TTL = 300  # 默认 5 分钟
        # 连续失败计数 (熔断用)
        self._fail_count: dict[str, int] = {}
        self._FAIL_THRESHOLD = 5   # 连续失败 5 次熔断
        self._BREAK_DURATION = 600  # 熔断 10 分钟
        self._broken_until: dict[str, float] = {}  # monotonic
        # V30（审计 2026-10-04）：locator 抛错的「查不到」也要有账，否则「持续失败」这回事不存在。
        self._presence_fail_count = 0
        self._PRESENCE_FAIL_THRESHOLD = 3

    def _load_cooldowns(self, snapshot: str = ""):
        """从 SQLite 读回未过窗的冷却；`snapshot` 非空时先把旧 JSON 搬进库（只搬一次）。
        读库失败⛔ 折成"没有冷却"——那种读法把「查不到」变成「全员没冷却」。
        接住异常、进台账、保住进程内状态，响亮但不打断启动。
        """
        try:
            self._last_fired.update(repo.load_cooldowns())
        except Exception as e:
            logger.warning("load cooldowns from sqlite failed: %s: %s"
                           " (this process starts with an empty cooldown map)",
                           type(e).__name__, str(e)[:200])
            write_failures.record("triggers/engine.load_cooldowns",
                                  "%s: %s" % (type(e).__name__, str(e)[:200]),
                                  table="trigger_cooldowns")
        if snapshot:
            self._import_legacy_snapshot(snapshot)

    def _import_legacy_snapshot(self, path: str) -> None:
        """迁移腿：旧 `trigger_cooldowns.json` 只读这一次，**验过才退役**。
        两笔来源都可能是最新的 ⇒ 同键取较新那个（更长的冷却＝⛔ 提前放行）。
        删文件是不可逆动作，挂在「写进去并且读回来逐键对得上」之后：验证不过就把快照留着，
        下次启动再搬一遍（整段幂等），⛔ 把用户的活数据删在半路。
        """
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            logger.warning("cooldown snapshot unreadable, kept: %s (%s)", path, e)
            return
        now = time.time()
        fresh = {k: float(v) for k, v in data.items()
                 if isinstance(v, (int, float)) and now - v < repo.COOLDOWN_RETENTION_S}
        if not fresh:
            logger.info("cooldown snapshot %s carries nothing fresh", path)
            return
        for k, v in fresh.items():
            if v > self._last_fired.get(k, 0):
                self._last_fired[k] = v
        try:
            repo.save_cooldowns(self._last_fired)
            back = repo.load_cooldowns()
        except Exception as e:
            logger.warning("cooldown snapshot not persisted, kept: %s (%s)", path, e)
            write_failures.record("triggers/engine.import_legacy_snapshot",
                                  "%s: %s" % (type(e).__name__, str(e)[:200]),
                                  table="trigger_cooldowns")
            return
        missing = [k for k, v in fresh.items() if back.get(k) != v]
        if missing:
            logger.warning("cooldown snapshot kept: %d/%d key(s) unverified in sqlite: %s",
                           len(missing), len(fresh), missing[:5])
            return
        try:
            os.remove(path)
        except Exception as e:
            logger.warning("cooldown snapshot imported but unlink failed: %s (%s)", path, e)
            return
        logger.info("cooldown snapshot imported and retired: %s (%d entries)", path, len(fresh))

    def _save_cooldowns(self):
        """整张冷却表写进 SQLite；写失败＝loud＋进台账，但进程内状态⛔ 跟着丢。"""
        try:
            repo.save_cooldowns(self._last_fired)
        except Exception as e:
            logger.warning("save cooldowns failed: %s: %s", type(e).__name__, str(e)[:200])
            write_failures.record("triggers/engine.save_cooldowns",
                                  "%s: %s (cooldowns are in-memory only until this write works)"
                                  % (type(e).__name__, str(e)[:200]),
                                  table="trigger_cooldowns")

    def set_runtime(self, rt) -> None:
        self.rt = rt
        # 旧 JSON 只在这一步被读一次（迁移）；此后 engine ⛔ 再碰这枚文件
        data_dir = getattr(rt, "data_dir", "") or getattr(getattr(rt, "settings", None), "data_dir", "")
        self._load_cooldowns(os.path.join(data_dir, "trigger_cooldowns.json") if data_dir else "")

    # ---- 主入口 ----

    async def handle_event(self, event_type: str, payload: dict | None = None,
                           dry_run: bool = False) -> list[dict]:
        """处理一个事件，返回匹配并执行的 trigger 结果列表。

        payload 统一字段：event, member, room, confidence, timestamp, 及事件特有字段。

        dry_run=True（DCD 裁定 C）＝只评估不执行：闸门照过、动作不跑、冷却与熔断不写、
        事件防抖窗口不占、trigger_runs 执行流水不落。默认 False＝既有行为一字不变。
        """
        payload = dict(payload or {})
        payload.setdefault("event", event_type)
        payload.setdefault("timestamp", datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))

        # v2.6: 事件入口写入 trace 上下文，trigger 驱动的技能/发声与上游同链
        trace_id = self._trace_id()
        if not trace_id:
            trace_id = uuid.uuid4().hex[:16]
            try:
                from butler.core.agent import _current_trace_id
                _current_trace_id.set(trace_id)
            except Exception:
                pass

        # GATE 2: 事件级防抖 — 同 (event, member, room) 30s 内重复事件直接丢弃
        debounce_key = f"{event_type}:{payload.get('member','')}:{payload.get('room','')}"
        now_d = time.monotonic()   # 进程内窗口：墙钟前跳会把它当场重开
        last_seen = self._event_seen.get(debounce_key, 0)
        if now_d - last_seen < 30:
            logger.debug("event debounced: %s (%.0fs ago)", debounce_key, now_d - last_seen)
            return []
        if not dry_run:
            # 模拟不许偷走真实事件的 30s 窗口：试一次就把下一次真事件吞掉＝工具咬人
            self._event_seen[debounce_key] = now_d
        # 清理过期防抖记录
        self._event_seen = {k: v for k, v in self._event_seen.items() if now_d - v < 120}

        _eval_t0 = time.monotonic()
        matched = self._match(event_type, payload)
        await self._record_evaluation(event_type, len(matched),
                                      int((time.monotonic() - _eval_t0) * 1000), dry_run, trace_id)
        if matched:
            logger.info("trigger chain: event=%s trace=%s matched=%d", event_type, trace_id, len(matched))
        if not matched:
            return []

        results = []
        for trig in matched:
            if not await self._check_presence(trig):
                logger.info("trigger %s skipped: require_presence not satisfied", trig["id"])
                continue
            try:
                res = await self._fire(trig, payload, dry_run=dry_run)
                results.append(res)
            except Exception as e:
                logger.exception("trigger %s fire failed", trig["id"])
                results.append({"trigger_id": trig["id"], "ok": False, "error": str(e)})
            # v0.3 互斥语义：matched 已按 priority 降序，高优先级 trigger 执行后，
            # 同一事件默认不再叠加其他 trigger（避免 morning_lidicn 与 morning_greet 重复打招呼）。
            # trigger 显式 exclusive=False 时允许与后续 trigger 叠加。
            if trig.get("exclusive", True):
                break
        return results

    async def _record_evaluation(self, event_type: str, matched_count: int,
                                 duration_ms: int, dry_run: bool, trace_id: str) -> None:
        """格2（DCD 裁定 20261001 格2 A）：每次走到匹配器记一行，含 0 命中。

        静默吞异常＝向「评估了多少次」惎报 0，所以写失败必须留痕；而 locked 分支会顺带点名
        持锁连接（`write_failures.record` 的 census 腿，10-01 第二窗靠它）。
        """
        try:
            from butler.store import repo
            await asyncio.to_thread(repo.add_trigger_evaluation,
                                    event_type, matched_count, duration_ms, dry_run, trace_id)
        except Exception as e:
            logger.warning("trigger_evaluation persist failed: %s: %s (event=%s trace=%s)",
                           type(e).__name__, str(e)[:200], event_type, trace_id)
            write_failures.record("triggers/engine.add_trigger_evaluation",
                                  "%s: %s" % (type(e).__name__, str(e)[:200]),
                                  table="trigger_evaluations", trace_id=trace_id)

    @staticmethod
    def _trace_id() -> str:
        """当前链路 trace_id；未设返回空串，不抛。"""
        try:
            from butler.core.agent import _current_trace_id
            return _current_trace_id.get() or ""
        except Exception:
            return ""

    # ---- 匹配 ----

    def _match(self, event_type: str, payload: dict) -> list[dict]:
        """筛选 enabled + 事件类型 + 条件 + 冷却都通过的 trigger，按 priority 降序。"""
        now = time.time()          # 墙钟：_last_fired 是 epoch 域（落盘、daily 比日期）
        now_m = time.monotonic()   # 熔断窗口是进程内时长
        out = []
        for trig in self.store.list():
            if not trig.get("enabled", True):
                continue
            if trig["event"] != event_type:
                continue
            if not self._check_conditions(trig, payload):
                continue
            # 冷却（per-member）
            member = str(payload.get("member") or payload.get("name") or "")
            cd_key = trig["id"] + (":" + member if member else "")

            # 熔断检查
            broken = self._broken_until.get(cd_key, 0)
            if broken > now_m:
                logger.warning("trigger %s[%s] circuit broken (%.0fs left)", trig["id"], member or "global", broken - now_m)
                continue

            last = self._last_fired.get(cd_key, 0)
            cd_type = trig.get("cooldown_type", "")
            if cd_type == "daily":
                today = datetime.now().strftime("%Y-%m-%d")
                last_date = time.strftime("%Y-%m-%d", time.localtime(last)) if last else ""
                if last_date == today:
                    logger.debug("trigger %s[%s] already fired today", trig["id"], member or "global")
                    continue
            else:
                cd = trig.get("cooldown_sec", 300)
                if cd > 0 and now - last < cd:
                    logger.debug("trigger %s[%s] in cooldown (%.0fs left)", trig["id"], member or "global", cd - (now - last))
                    continue
            out.append(trig)
        # priority 已在 store.list() 排序，这里保持
        return out

    def _check_conditions(self, trig: dict, payload: dict) -> bool:
        cond = trig.get("conditions") or {}

        # 时间窗口
        tr = cond.get("time_range", "")
        if tr and not self._in_time_range(tr):
            return False

        # 成员匹配（空表示不限制）
        want_member = cond.get("member", "")
        if want_member:
            got = str(payload.get("member") or payload.get("name") or "")
            if want_member != got and want_member not in got.split(","):
                return False

        # 房间匹配（空表示不限制）
        want_room = cond.get("room", "")
        if want_room:
            got = str(payload.get("room") or "")
            if want_room != got:
                return False

        # 按钮 ID 匹配（button_pressed 事件用：指定按钮才触发）
        want_button = cond.get("button_id", "")
        if want_button:
            got_btn = str(payload.get("button_id") or "")
            if want_button != got_btn:
                return False

        # 文本包含匹配（voice_wake 事件用：用户说的话包含特定短语则触发）
        text_contains = cond.get("text_contains") or []
        if text_contains:
            got_text = str(payload.get("text") or payload.get("message") or "")
            if not got_text:
                return False
            if not any(kw in got_text for kw in text_contains):
                return False

        return True

    @staticmethod
    def _in_time_range(time_range: str) -> bool:
        """判断当前时间是否在 HH:MM-HH:MM 范围内（支持跨午夜，如 22:00-06:00）。"""
        m = re.match(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$", time_range.strip())
        if not m:
            return True
        h1, m1, h2, m2 = map(int, m.groups())
        now = datetime.now()
        cur = now.hour * 60 + now.minute
        start = h1 * 60 + m1
        end = h2 * 60 + m2
        if start <= end:
            return start <= cur <= end
        # 跨午夜
        return cur >= start or cur <= end

    # ---- 在场校验（v0.4 require_presence 条件） ----

    async def _check_presence(self, trig: dict) -> bool:
        """conditions.require_presence：触发前先确认目标成员在场，否则跳过。

        格式：{"member": "Kevin"}（指定成员，须 locator 能找到）；"" / 空 dict = 只要有人在即可。
        locator 未就绪时不拦截（保守放行，避免误杀既有 trigger）；locator 起来了却**连续**报错，
        达 `_PRESENCE_FAIL_THRESHOLD` 后改判跳过（V30：⛔ 把「查不到」永久读成「在场」）。
        """
        cond = trig.get("conditions") or {}
        rp = cond.get("require_presence")
        if not rp:
            return True
        if self.rt is None or getattr(self.rt, "locator", None) is None:
            return True
        member = rp.get("member") if isinstance(rp, dict) else rp
        try:
            res = await self.rt.locator.find_member(member or "")
        except Exception as e:
            self._presence_fail_count += 1
            logger.warning(
                "trigger %s require_presence check failed (%d/%d consecutive): %s",
                trig["id"], self._presence_fail_count, self._PRESENCE_FAIL_THRESHOLD, e)
            # 兜底方向（V30）：未达阈值仍放行——一次抖动 ⛔ 误杀既有 trigger；
            # 达阈值改判跳过——⛔ 无限期把「查不到」读成「在场」，那是把静默失败当健康。
            return self._presence_fail_count < self._PRESENCE_FAIL_THRESHOLD
        self._presence_fail_count = 0
        return bool(res.get("found"))

    # ---- 执行 ----

    async def _fire(self, trig: dict, payload: dict, dry_run: bool = False) -> dict:
        """执行一个 trigger 的所有动作（顺序链）。

        闸门：并发锁 + 设备锁 + 成功后才写冷却 + 失败熔断。

        dry_run=True：三道闸照常判定（模拟要说真话），过完闸即返回——
        不加运行/资源锁、不执行技能、不写冷却退避、不落 trigger_runs。
        """
        member = str(payload.get("member") or payload.get("name") or "")
        cd_key = trig["id"] + (":" + member if member else "")

        # GATE 1: 并发执行锁 — 同一 trigger 正在跑就跳过
        if cd_key in self._running:
            logger.warning("trigger %s already running, skip concurrent fire", cd_key)
            return {"trigger_id": trig["id"], "ok": False, "error": "already_running", "skipped": True}

        # GATE 3: 输出资源锁 — 检查 TV/音箱是否被占用（v2.8: resources 声明，多资源任一占用即跳过）
        lock_keys = [str(x).strip() for x in (trig.get("resources") or []) if isinstance(x, str) and str(x).strip()]
        legacy = trig.get("_output_lock", "")
        if legacy and legacy not in lock_keys:
            lock_keys.append(legacy)
        if lock_keys:
            now_l = time.monotonic()
            for lk in lock_keys:
                locked_until = self._device_locks.get(lk, 0)
                if locked_until > now_l:
                    logger.warning("trigger %s resource %s locked for %.0fs more, skip",
                                   trig["id"], lk, locked_until - now_l)
                    return {"trigger_id": trig["id"], "ok": False, "error": "device_locked",
                            "locked_until": locked_until, "skipped": True}

        if dry_run:
            planned = [{"skill": a.get("skill", ""),
                        "params": self._render_params(a.get("params", {}), payload)}
                       for a in trig.get("actions", [])]
            logger.info("trigger DRY-RUN %s: would run %d action(s), side effects skipped",
                        trig["id"], len(planned))
            return {"trigger_id": trig["id"], "ok": bool(planned), "dry_run": True,
                    "actions": [], "actions_count": 0, "planned_actions": planned,
                    "would_cooldown_sec": trig.get("cooldown_sec", 300)}

        self._running.add(cd_key)
        fire_start = time.time()         # epoch：写进 _last_fired（落盘 + MCP 的 last_run_at）
        fire_start_m = time.monotonic()  # 时长与资源锁 TTL
        logger.info("trigger fired: %s (event=%s, member=%s, room=%s)",
                    trig["id"], payload.get("event"), payload.get("member"), payload.get("room"))

        # 占用输出资源锁
        for lk in lock_keys:
            self._device_locks[lk] = fire_start_m + self._DEVICE_LOCK_TTL
            logger.info("resource lock acquired: %s (%.0fs TTL)", lk, self._DEVICE_LOCK_TTL)

        continue_on_error = trig.get("continue_on_error", True)
        action_results = []
        try:
            for action in trig.get("actions", []):
                skill_id = action.get("skill", "")
                params = self._render_params(action.get("params", {}), payload)
                logger.info("trigger %s running action: skill=%s params=%s",
                            trig["id"], skill_id, {k: v for k, v in params.items() if k != "_trigger_member"})
                res = await self._run_skill(skill_id, params, trig,
                                            event_room=payload.get("room") or "",
                                            event_member=payload.get("member") or "")
                action_results.append(res)
                if not res.get("ok"):
                    if res.get("status") == "quarantined":
                        # 格3②：人工终态只报可见，不报成运行失败
                        logger.warning("trigger %s action skipped: skill=%s quarantined (%s)",
                                       trig["id"], skill_id, res.get("error", ""))
                    else:
                        logger.error("trigger %s action FAILED: skill=%s error=%s",
                                     trig["id"], skill_id, res.get("error", "unknown"))
                    if not continue_on_error:
                        logger.warning("trigger %s chain stopped at %s (continue_on_error=false)",
                                       trig["id"], skill_id)
                        break
        finally:
            self._running.discard(cd_key)
            # 释放设备/资源锁
            for lk in lock_keys:
                self._device_locks.pop(lk, None)
                logger.info("resource lock released: %s", lk)

        result = {
            "trigger_id": trig["id"],
            "ok": all(r.get("ok") for r in action_results) if action_results else False,
            "actions": action_results,
        }

        # GATE 4: 冷却只在成功后写；失败记短退避 + 熔断计数
        elapsed = time.monotonic() - fire_start_m
        if result["ok"]:
            self._last_fired[cd_key] = fire_start
            self._fail_count[cd_key] = 0
            logger.info("trigger %s completed ok in %.1fs", trig["id"], elapsed)
        elif _only_terminal(action_results):
            # 格3②：失败全来自隔离态 ⇒ 不进熔断计数、不写退避、不熔断
            logger.warning("trigger %s skipped: 隔离态人工终态，不计入熔断（fail_count 保持 %d）",
                           trig["id"], self._fail_count.get(cd_key, 0))
        else:
            # 失败：短退避 60s（不消耗完整冷却），累计失败计数
            # 退避从此刻起算（⛔ 从 fire_start 起算：elapsed 一加上去就整枚逃逸）
            self._last_fired[cd_key] = time.time() - trig.get("cooldown_sec", 300) + 60
            self._fail_count[cd_key] = self._fail_count.get(cd_key, 0) + 1
            fail_n = self._fail_count[cd_key]
            logger.warning("trigger %s failed (%d/%d), backoff 60s",
                           trig["id"], fail_n, self._FAIL_THRESHOLD)
            if fail_n >= self._FAIL_THRESHOLD:
                self._broken_until[cd_key] = time.monotonic() + self._BREAK_DURATION
                logger.error("trigger %s CIRCUIT BROKEN for %ds (%d consecutive failures)",
                             trig["id"], self._BREAK_DURATION, fail_n)
        self._save_cooldowns()

        # v0.7: 记录 trigger 执行日志
        try:
            from butler.store import repo
            await asyncio.to_thread(repo.add_trigger_run,
                trig["id"], str(payload.get("event") or ""),
                "ok" if result["ok"] else "error",
                [r.get("skill") for r in action_results],
                f"elapsed={elapsed:.1f}s" + ("" if result["ok"] else f", errors={[r.get('error') for r in action_results if not r.get('ok')]}"), trace_id=self._trace_id())
        except Exception as e:
            # v2.6#3：静默吞异常＝向面板谎报"没有触发历史"，必须留痕
            logger.warning("trigger_run persist failed: %s: %s (trigger=%s trace=%s)",
                           type(e).__name__, str(e)[:200], trig["id"], self._trace_id())
            write_failures.record("triggers/engine.add_trigger_run",
                                  "%s: %s" % (type(e).__name__, str(e)[:200]),
                                  table="trigger_runs", trace_id=self._trace_id())
        return result

    async def _run_skill(self, skill_id: str, params: dict, trig: dict,
                          event_room: str = "", event_member: str = "") -> dict:
        """调用技能执行器。runtime 未就绪时返回错误。

        _trigger_room/_trigger_member 优先级：动作 params > 事件 payload > trigger conditions。
        技能引擎据此把 meta["room"] 写回，runner 按所在房间过滤输出设备。
        """
        if self.rt is None or getattr(self.rt, "runner", None) is None:
            return {"skill": skill_id, "ok": False, "error": "runtime not ready"}
        try:
            # 把 trigger 的 role/room/member 注入 payload，供技能引擎/输出分发使用
            payload = dict(params)
            payload["_trigger_role"] = trig.get("role", "butler")
            payload["_trigger_id"] = trig["id"]
            payload["_trigger_room"] = (params.get("room") or event_room
                                        or trig.get("conditions", {}).get("room", ""))
            payload["_trigger_member"] = (params.get("member") or event_member or "")
            result = await self.rt.runner.run(
                skill_id, source="trigger", payload=payload,
            )
            return {"skill": skill_id, "ok": result.get("ok", False),
                    "status": result.get("status"), "error": result.get("error")}
        except Exception as e:
            logger.exception("trigger action skill %s failed", skill_id)
            return {"skill": skill_id, "ok": False, "error": str(e)}

    @staticmethod
    def _render_params(params: dict, payload: dict) -> dict:
        """渲染 {{event.xxx}} 模板，递归处理 dict 和 str 值。"""
        out = {}
        for k, v in params.items():
            if isinstance(v, str):
                out[k] = _PARAM_RE.sub(lambda m: str(payload.get(m.group(1), "")), v)
            elif isinstance(v, dict):
                out[k] = TriggerEngine._render_params(v, payload)
            else:
                out[k] = v
        return out

    # ---- 状态查询 ----

    def status(self) -> dict:
        """返回所有 trigger 的匹配/冷却状态（供 API/调试）。"""
        now = time.time()   # 与 _last_fired 同域；跨时基相减会报出「还剩 55 年」
        out = {}
        for trig in self.store.list():
            # 读侧键形制必须＝写侧（`:244`/`:358`：`id` 或 `id:member`）。裸 id 只命中无 member
            # 的事件⇒带 member 的触发器恒报「没在冷却」，而同一秒 `_match()` 真的在拦它＝显示
            # 与执行相反（台账 §14-6；§15-2 现读：本函数零生产调用方，修的是「接上它即拿到假话」）。
            # 多枚 member 键取最新＝最保守：宁可说「还在冷却」，⛔ 谎报「随时可再火」。
            tid = trig["id"]
            last = max([v for k, v in self._last_fired.items()
                        if k == tid or k.startswith(tid + ":")] or [0])
            cd = trig.get("cooldown_sec", 300)
            out[trig["id"]] = {
                "enabled": trig.get("enabled", True),
                "event": trig["event"],
                "cooldown_remaining": max(0.0, cd - (now - last)) if last > 0 else 0.0,
                "last_fired_ago": round(now - last, 1) if last > 0 else None,
            }
        return out
