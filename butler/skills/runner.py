"""技能调度器：统一入口（HTTP/MQTT）→ 熔断 → 引擎执行 → 输出互斥 → 推送 → 落库/广播。

安全阀（编排层出问题时保护管家）：
- 滑动窗口熔断：同技能 60s 内 >5 次触发 → 429 + 冷却（300s 起指数退避）
- 全局并发 1：VLM 类技能串行（asyncio.Semaphore）
- 每技能每日硬上限（skill_runs 当日计数）
- 输出互斥：播报中按 on_busy 入队（最多 1 个）或丢弃
- 内容去重：brain.dedup=true 时接现有 bigram DedupChecker
"""
from __future__ import annotations

import asyncio
import time
from collections import deque

# v1.3 隔离阈值
QUARANTINE_THRESHOLDS = {
    "consecutive_failures": 3,
    "breaker_trips": 3,
    "timeout_ms": 30000,
    "timeout_count": 2,
    "avg_duration_ms": 15000,
    "avg_duration_count": 5,
}

from butler.logging_setup import get_logger
from butler.store import repo, write_failures
from butler.skills.runner_types import SkillContext, SkillResult
from butler.modes.engine import gate_skill

logger = get_logger("butler.skills.runner")

_WINDOW_S = 60
_WINDOW_MAX = 5
_COOLDOWN_BASE_S = 300

# 批24（表行 8 RUF006 那族）：loop 对被调度任务只持弱引用⇒fire-and-forget 的 task 必须存进
# 这张强引用注册表，跑完由 done_callback 自己摘掉（同批18 dialog._idle_tasks／批21 cron_task）。
_BG_TASKS: set = set()


class SkillRunner:
    def __init__(self, settings, store, registry, dedup=None):
        self.s = settings
        self.store = store
        self.registry = registry
        self.dedup = dedup
        self._sem = asyncio.Semaphore(1)          # 全局 VLM 串行
        self._out_lock = asyncio.Lock()           # 输出互斥
        self._queued = False                      # on_busy=queue 的待播标记
        self._hits: dict[str, deque] = {}
        self._cooldown_until: dict[str, float] = {}
        self._backoff: dict[str, int] = {}
        self.quarantine_mgr = None  # 由 app.py 注入（v1.3）

    # ---- 熔断 ----

    def _breaker_check(self, skill_id: str) -> tuple[bool, float]:
        """返回 (allowed, cooldown_remaining_s)。"""
        now = time.monotonic()
        cd = self._cooldown_until.get(skill_id, 0)
        if now < cd:
            return False, cd - now
        hits = self._hits.setdefault(skill_id, deque())
        while hits and now - hits[0] > _WINDOW_S:
            hits.popleft()
        if len(hits) >= _WINDOW_MAX:
            cool = _COOLDOWN_BASE_S * (2 ** self._backoff.get(skill_id, 0))
            self._cooldown_until[skill_id] = now + cool
            self._backoff[skill_id] = min(self._backoff.get(skill_id, 0) + 1, 5)
            hits.clear()
            logger.warning("breaker open: %s cooldown=%.0fs", skill_id, cool)
            return False, cool
        hits.append(now)
        self._backoff[skill_id] = 0
        return True, 0

    def breaker_status(self) -> dict:
        now = time.monotonic()
        out = {}
        for skill in self.store.list():
            sid = skill["id"]
            cd = self._cooldown_until.get(sid, 0)
            hits = self._hits.get(sid) or deque()
            recent = sum(1 for t in hits if now - t <= _WINDOW_S)
            out[sid] = {
                "window_hits": recent,
                "window_max": _WINDOW_MAX,
                "cooldown_remaining": max(0.0, cd - now) if cd > now else 0.0,
                "tripped": cd > now,
            }
        return out

    # ---- 失败计数与自动隔离（v1.3 预留，v1.1 基础框架）----

    def _record_failure(self, skill_id: str) -> None:
        """记录执行失败，连续失败达到阈值时自动隔离。"""
        if not hasattr(self, "_fail_counts"):
            self._fail_counts = {}
        count = self._fail_counts.get(skill_id, 0) + 1
        self._fail_counts[skill_id] = count
        if count >= 3:
            skill = self.store.get(skill_id)
            if skill and skill.get("status") != "quarantined":
                self.store.quarantine(skill_id, f"连续 {count} 次执行失败自动隔离")
                self._fail_counts[skill_id] = 0

    def _record_success(self, skill_id: str) -> None:
        """记录执行成功，重置失败计数。"""
        if hasattr(self, "_fail_counts"):
            self._fail_counts[skill_id] = 0

    # ---- 运行历史 ----

    @staticmethod
    def _persist_run(skill_id: str, source: str, status: str, duration_ms: int,
                     text: str, error: str, meta: dict) -> None:
        try:
            # v2.6: 从 contextvar 取 trace_id，关联全链路
            trace_id = ""
            try:
                from butler.core.agent import _current_trace_id
                trace_id = _current_trace_id.get() or ""
            except Exception:
                pass
            repo.add_skill_run(skill_id, source, status, duration_ms, text[:500],
                               error[:300], meta, trace_id=trace_id)
        except Exception as e:
            logger.warning("skill_runs persist failed: %s", e)
            write_failures.record("skills/runner.add_skill_run",
                                  "%s: %s" % (type(e).__name__, str(e)[:200]),
                                  table="skill_runs")

    @staticmethod
    def _publish_done(rt, skill_id: str, result: dict) -> None:
        try:
            if rt and rt.mqtt:
                from butler.bus.topics import skill_done_topic
                rt.mqtt.publish(skill_done_topic(skill_id), result)
        except Exception as e:
            logger.warning("publish skill done failed: %s", e)

    # ---- 主入口 ----

    async def run(self, skill_id: str, source: str = "api", dry_run: bool = False,
                  payload: dict | None = None, force: bool = False) -> dict:
        """运行技能。force=True 跳过 enabled 检查（兼容端点用）。"""
        skill = self.store.get(skill_id)
        if skill is None:
            return {"ok": False, "error": "skill not found", "status": "not_found"}
        status = skill.get("status", "enabled")
        if status == "quarantined" and not force:
            reason = skill.get("quarantine_reason", "")
            return {"ok": False, "error": f"skill quarantined: {reason}", "status": "quarantined"}
        if status == "draft" and not force:
            return {"ok": False, "error": "skill is draft (not confirmed)", "status": "draft"}
        # v1.5 审批检查：pending_review / rejected 的技能不能运行（force 除外）
        approval = skill.get("approval", "approved")
        if approval == "pending_review" and not force and not dry_run:
            return {"ok": False, "error": "skill pending review (沙箱审批中)", "status": "pending_review"}
        if approval == "rejected" and not force:
            return {"ok": False, "error": "skill rejected (已被拒绝)", "status": "rejected"}
        if not skill.get("enabled") and not (dry_run or force):
            return {"ok": False, "error": "skill disabled", "status": "disabled"}

        # DCD 裁定② 20261002：模式行为规则接上技能执行入口（三入口的第三处）。
        # force／dry_run＝人工按下的立即执行与试运行，按本文件既有口径跳过编排层检查（⛔ 新造例外）。
        # 技能类别只有 _guess_skill_category(skill_id) 一条路（对 id 做子串匹配）；在册技能的 id 现量无一
        # 命中 emergency/anomaly 词表（读数与复跑见 scripts/audit_1002/census_mode_gate_radius_1002.py）
        # ⇒ 非 daily 档会挡光的根源是「类别猜不中」，把「哪条技能算紧急」显式化已另投待裁。
        if not (dry_run or force):
            allowed, mode = gate_skill(skill_id)
            if not allowed:
                logger.info("SKILL_BLOCKED mode=%s skill=%s source=%s", mode, skill_id, source)
                return {"ok": False, "error": f"当前模式（{mode}）不允许执行该技能",
                        "status": "mode_blocked"}

        # 安全违规检测（v1.3）
        if self.quarantine_mgr and not dry_run:
            safe, reason = self.quarantine_mgr.check_security(skill)
            if not safe:
                self.quarantine_mgr.quarantine(skill_id, reason)
                return {"ok": False, "error": reason, "status": "quarantined"}

        allowed, cooldown = self._breaker_check(skill_id)
        if not allowed:
            if self.quarantine_mgr and not dry_run:
                self.quarantine_mgr.record_breaker_trip(skill_id)
            res = {"ok": False, "error": f"breaker cooldown {cooldown:.0f}s",
                   "status": "breaker", "retry_after": round(cooldown)}
            self._persist_run(skill_id, source, "breaker", 0, "", res["error"], {})
            return res

        per_day = (skill.get("limits") or {}).get("per_day", 0)
        if per_day and not dry_run:
            try:
                today = repo.count_skill_runs_today(skill_id, ok_only=True)
                if today >= per_day:
                    res = {"ok": False, "error": f"daily limit {per_day} reached",
                           "status": "daily_limit"}
                    self._persist_run(skill_id, source, "daily_limit", 0, "", res["error"], {})
                    return res
            except Exception as e:
                logger.warning("daily count failed: %s", e)

        engine = self.registry.get((skill.get("brain") or {}).get("engine", ""))
        if engine is None:
            if self.quarantine_mgr and not dry_run:
                self.quarantine_mgr.quarantine(skill_id, f"依赖缺失：引擎不存在")
            return {"ok": False, "error": "engine not found", "status": "engine_missing"}

        ctx = SkillContext(
            skill=skill, source=source, dry_run=dry_run,
            payload=payload or {}, rt=getattr(self, "rt", None),
            now=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        # 解析角色（多角色对话核心），供输出分发与对话线程使用
        rt0 = getattr(self, "rt", None)
        if rt0 is not None and getattr(rt0, "roles", None) is not None:
            ctx.role = rt0.roles.resolve_for_skill(skill)
        t0 = time.monotonic()
        try:
            async with self._sem:
                result = await asyncio.wait_for(
                    engine.run(ctx),
                    timeout=QUARANTINE_THRESHOLDS["timeout_ms"] / 1000,
                )
        except asyncio.TimeoutError:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.warning("skill %s execution timeout after %dms", skill_id, duration_ms)
            if self.quarantine_mgr and not dry_run:
                self.quarantine_mgr.record_timeout(skill_id, duration_ms)
            result = SkillResult(ok=False, error=f"执行超时（>{QUARANTINE_THRESHOLDS['timeout_ms']}ms）", status="timeout")
        except Exception as e:
            logger.exception("skill %s engine error", skill_id)
            result = SkillResult(ok=False, error=str(e), status="error")
        duration_ms = int((time.monotonic() - t0) * 1000)

        # 记录执行耗时用于资源占用检测（v1.3）
        if self.quarantine_mgr and not dry_run and result.ok:
            self.quarantine_mgr.record_duration(skill_id, duration_ms)

        meta = dict(result.meta or {})
        meta["duration_ms"] = duration_ms

        # 输出：试跑/失败/引擎声明跳过 → 不推送
        if dry_run:
            status = "dry"
        elif not result.ok or not result.text:
            status = result.status or "error"
        else:
            status = result.status or "ok"

        if not dry_run and result.ok and result.text:
            await self._apply_outputs(skill, result, meta)
            self._record_success(skill_id)
        elif not dry_run and not result.ok:
            self._record_failure(skill_id)

        response = {
            "ok": result.ok,
            "skill_id": skill_id,
            "text": result.text,
            "status": status,
            "error": result.error,
            "meta": meta,
            "dry_run": dry_run,
        }
        self._persist_run(skill_id, source, status, duration_ms,
                          result.text, result.error, meta)
        # v1.8 性能监控埋点
        try:
            from butler.runtime import get_runtime
            rt = get_runtime()
            perf = getattr(rt, "perf_monitor", None)
            if perf and not dry_run:
                perf.record_skill_run(skill_id, float(duration_ms), result.ok, result.error or "", source)
        except Exception as e:
            # P2-9 ②类（批42 组11）：埋点一抛这次运行的耗时与成败就进不了性能账，⛔ 无声＝面板少一行、慢和错都看不见，而技能照常报 ok
            logger.warning("skill perf record failed skill=%s err=%s", skill_id, e)
        if not dry_run:
            self._publish_done(getattr(self, "rt", None), skill_id, response)
        return response

    # ---- 输出 ----

    async def _apply_outputs(self, skill: dict, result: SkillResult, meta: dict) -> None:
        """输出互斥 + 内容去重 + 角色驱动分发。

        分发策略：
        - 角色若配置了 output_devices：以角色设备为准（tv=音频URL播放；xiaomi=HA 文本推送），
          实现「多角色各从指定设备出声、全屋任意小爱」。
        - 角色无 output_devices：回退技能 output 类型（tv_notify/xiaomi_speak/bark）兼容旧技能。
        - meta.suppress_output=True：引擎自行完成输出分发（如决策层），跳过本方法。
        """
        if meta.get("suppress_output"):
            return
        on_busy = skill.get("on_busy", "drop")
        if self._out_lock.locked():
            if on_busy == "queue" and not self._queued:
                self._queued = True
                logger.info("skill %s queued (output busy)", skill["id"])
            else:
                meta["on_busy"] = "dropped"
                logger.info("skill %s dropped (output busy)", skill["id"])
                return
        async with self._out_lock:
            self._queued = False
            rt = getattr(self, "rt", None)
            if rt is None:
                return

            # 内容去重（bigram）：仅对声明 dedup 的技能
            if (skill.get("brain") or {}).get("dedup") and self.dedup is not None:
                member = str(meta.get("person") or "doubao")
                try:
                    dup, sim = await self.dedup.is_duplicate(member, result.text)
                    if dup:
                        meta["dedup"] = round(sim, 2)
                        logger.info("skill %s dedup skip (sim=%.2f)", skill["id"], sim)
                        return
                except Exception as e:
                    logger.warning("dedup check failed: %s", e)

            # 角色解析（多角色对话核心）
            role = None
            if getattr(rt, "roles", None) is not None:
                role = rt.roles.resolve_for_skill(skill)
            meta["role"] = role.id if role else "butler"

            if role and role.output_devices:
                await self._dispatch_role(rt, role, skill, result, meta)
            else:
                for out in skill.get("output") or []:
                    otype = out.get("type")
                    try:
                        if otype == "tv_notify":
                            await self._push_tv(rt, skill, result, meta, tts=out.get("tts", True), role=role)
                        elif otype == "xiaomi_speak":
                            await rt.ha.notify_message(result.text, rt.settings.xiaomi_notify_entity)
                        elif otype == "bark":
                            await rt.bark.push(result.text)
                    except Exception as e:
                        logger.warning("skill %s output %s failed: %s", skill["id"], otype, e)

            # v0.6: 技能结果推送到豆包app对应角色对话（push_to_app=true 时）
            if skill.get("push_to_app") and getattr(rt, "notifier", None) is not None and role is not None:
                try:
                    await rt.notifier.push(role.id, f"技能 {skill.get('name', skill['id'])} 结果：{result.text}")
                    meta["pushed_to_app"] = True
                except Exception as e:
                    logger.warning("push_to_app failed for %s: %s", skill["id"], e)

    async def _dispatch_role(self, rt, role, skill: dict, result: SkillResult, meta: dict) -> None:
        """按角色 output_devices 分发（tv=音频URL播放；xiaomi=HA 文本推送到指定实体）。

        角色决定「从哪个设备出声」：晓月仅 xiao_living_right，主角色 tv_living+xiao_living。
        """
        tts = True
        for out in skill.get("output") or []:
            if out.get("type") == "tv_notify":
                tts = out.get("tts", True)
        # v0.3: meta["room"] 存在时按事件房间过滤设备（trigger 动作链：只在所在房间小爱出声）
        _room = meta.get("room") or None
        devices = rt.devices.resolve(role.output_devices, _room) if getattr(rt, "devices", None) else []
        if not devices and _room:
            logger.info("role %s has no device in room %s, fallback to all", role.id, _room)
            devices = rt.devices.resolve(role.output_devices) if getattr(rt, "devices", None) else []
        dispatched: list[dict] = []
        if not devices:
            logger.warning("role %s has no available devices, fallback=%s", role.id, role.fallback)
            await self._role_fallback(rt, role, skill, result, meta)
            return
        for dev in devices:
            try:
                if dev.type == "tv":
                    await self._push_tv(rt, skill, result, meta, tts=tts, role=role)
                    dispatched.append({"device": dev.id, "type": "tv", "ok": True})
                elif dev.type == "xiaomi":
                    detail = await rt.ha.notify_message(result.text, dev.ha_entity or None)
                    dispatched.append({"device": dev.id, "type": "xiaomi", "ok": detail == "ok", "detail": detail})
            except Exception as e:
                logger.warning("role %s device %s failed: %s", role.id, dev.id, e)
                dispatched.append({"device": dev.id, "type": dev.type, "ok": False, "error": str(e)[:200]})
        meta["dispatched"] = dispatched

    async def _role_fallback(self, rt, role, skill: dict, result: SkillResult, meta: dict) -> None:
        """角色设备全不可用时的兜底：silent / tv / bark。"""
        fb = role.fallback or "bark"
        if fb == "silent":
            meta["fallback"] = "silent"
            return
        if fb == "tv":
            await self._push_tv(rt, skill, result, meta, tts=False, role=role)
            meta["fallback"] = "tv"
            return
        try:
            await rt.bark.push(result.text)
            meta["fallback"] = "bark"
        except Exception as e:
            logger.warning("role %s bark fallback failed: %s", role.id, e)

    @staticmethod
    async def _push_tv(rt, skill: dict, result: SkillResult, meta: dict, tts: bool, role=None) -> None:
        """电视弹窗 + 可选 TTS。TV 离线时降级 Bark 文字推送。

        role 传入时按其 voice/tts_backend 合成（实现「角色音色」）。
        """
        if not meta.get("tv_online", True):
            try:
                await rt.bark.push(result.text)
                meta["fallback"] = "bark"
            except Exception as e:
                logger.warning("bark fallback failed: %s", e)
            return
        tts_url = ""
        if tts:
            try:
                res = await rt.tts.synthesize(
                    result.text,
                    voice=role.voice if role else None,
                    backend=role.tts_backend if role else None,
                )
                if res:
                    tts_url = res.public_url
            except Exception as e:
                logger.warning("skill tts failed: %s", e)
        avatar = str(meta.get("person") or "doubao")
        rt.tv.notify({
            "title": skill.get("name", "豆包管家"),
            "content": result.text,
            "type": "info",
            "duration": 8000,
            "important": False,
            "tts_url": tts_url,
            "tts_volume": rt.settings.tts_volume,
            "pause_media": False,
            "avatar_url": f"{rt.settings.base_url.rstrip('/')}/avatars/{avatar}.png",
        })
        try:
            await asyncio.to_thread(
                repo.add_notify, avatar, skill.get("name", "豆包管家"), result.text,
                type="info", important=False, duration=8000, with_tts=tts,
                volume=rt.settings.tts_volume, pause_media=False,
                status="ok", tts_url=tts_url,
            )
        except Exception as e:
            logger.warning("skill add_notify failed: %s", e)

    # ---- MQTT 触发入口 ----

    def on_mqtt_trigger(self, topic: str, payload: dict) -> None:
        """butler/trigger/{skill_id} → 异步运行（发后不管）。"""
        skill_id = topic.rsplit("/", 1)[-1]
        if not self.store.get(skill_id):
            logger.warning("mqtt trigger unknown skill: %s", skill_id)
            return

        async def _run():
            try:
                await self.run(skill_id, source="mqtt", payload=payload or {})
            except Exception:
                logger.exception("mqtt trigger run %s failed", skill_id)

        # A4：投递要落回事件循环线程。生产唯一调用点是 app.py:133（`async def _consume` 内、
        # 跑在主循环上），那里的 get_event_loop() 已被弃用时序取代；call_soon_threadsafe
        # 继续留着，是给未来的线程调用者（如 paho 回调）留的口子。
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        try:
            if loop is not None:
                def _spawn_run():
                    task = loop.create_task(_run())
                    _BG_TASKS.add(task)
                    task.add_done_callback(_BG_TASKS.discard)

                loop.call_soon_threadsafe(_spawn_run)
            else:
                asyncio.run(_run())
        except Exception as e:
            logger.error("mqtt trigger schedule failed: %s", e)
