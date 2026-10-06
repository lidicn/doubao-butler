"""决策引擎主流程：摘要 → LLM 推理 → 白名单校验 → 安全过滤 → 行动执行 → 落库。

一次 tick（心跳）：
1. aggregator.build_summary(mock) → 状态摘要
2. llm.chat(system, [summary]) → 行动 JSON
3. 校验：
   - action 必须在白名单（speak/notify/reminder/suggest_automation），其余拒绝
   - confidence < threshold → 拒绝（status=filtered）
   - 夜间（night_start<=hour<night_end 跨午夜）只允许 notify
   - 冷却：同类行动（action+room+member）cooldown_minutes 内重复 → 拒绝
4. action_router.execute → 执行
5. add_decision_run 落库（含摘要，供复盘）
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime

from homesdk.time import house_now
from butler.logging_setup import get_logger
from butler.decision.action_router import ALLOWED_ACTIONS

logger = get_logger("butler.decision.engine")

_SYSTEM = """你是「豆包管家」家的家庭决策引擎。你每隔几分钟阅读一次全家状态快照，
判断是否有值得主动打扰家人的事情。家人：lidicn（理叔，40岁家长）、Kevin（12岁男孩）、Emily（10岁女孩）。

状态快照包含：时间、设备状态、成员在场、最近视觉事件、最近对话、今日活动。

决策规则：
1. 只有明显值得行动的事才行动；一切正常必须输出 no_action。
2. 行动类型白名单：
   - speak：通过指定房间小爱音箱主动播报（仅用于重要/即时的事，如叫醒、提醒出门）
   - notify：推送到豆包app角色对话（普通通知，如新闻摘要、低优先级提醒）
   - reminder：设定一个稍后提醒（如「15分钟后提醒吃药」）
   - suggest_automation：发现家人重复习惯时，建议创建自动化规则
3. 严禁操作任何设备（不允许开关灯/空调/电视等）。你只能播报、通知、提醒或建议。
4. 没人值得打扰时输出 no_action；不确定时输出 no_action。
5. 夜间（23:00-07:00）只允许 notify，禁止 speak/reminder。
6. 同一件事不要反复提醒：若快照显示已经提醒过，输出 no_action。

判断示例（不是唯一情形，供参考）：
- 早上 7-9 点，成员在其卧室/主卧（在场），客厅灯关着、卧室空调还开着、没有视觉事件：
  可能睡过头 → speak 到该成员所在房间叫醒
- 用户固定习惯时间点出现异常（如晚上 7 点没开客厅灯而主卧有人）→ 值得 notify
- 发现每天几乎同时出现的重复行为（如打开电脑时总开挂灯）→ suggest_automation
- 其他一切正常 → no_action

输出 JSON（只输出 JSON，不要任何解释或 markdown 代码块）：
{"action":"no_action|speak|notify|reminder|suggest_automation","role":"butler","room":"","member":"","text":"要说的话","reason":"判断理由","confidence":0.0-1.0}
字段说明：
- room：speak 目标房间（客厅/主卧室/书房/Kevin房间/Emily房间/卫生间），notify/reminder 可留空
- member：涉及的家庭成员（lidicn/Kevin/Emily），无特定成员可留空
- text：要说的话（speak 要口语化简短；notify 可稍长）
- confidence：0.0-1.0，低于 0.6 必须输出 no_action
- reason：一句话判断理由"""


class DecisionEngine:
    def __init__(self, rt, cfg, aggregator, router):
        self.rt = rt
        self.cfg = cfg
        self.aggregator = aggregator
        self.router = router

    async def tick(self, payload: dict | None = None) -> dict:
        """执行一次决策心跳。payload 可带 mock（测试用，跳过真实数据源）。"""
        payload = payload or {}
        if not self.cfg.enabled:
            return {"ok": False, "reason": "decision disabled", "action": "no_action"}

        start = time.time()

        # 1. 聚合
        mock = payload.get("mock") if isinstance(payload.get("mock"), dict) else None
        summary = await self.aggregator.build_summary(mock)
        logger.info("decision summary:\n%s", summary)

        # 2. LLM 推理
        action_json, err = await self._reason(summary)
        if action_json is None:
            self._log("error", summary=summary, status="error", filter_reason=err)
            return {"ok": False, "action": "error", "reason": err}

        action = action_json.get("action") or "no_action"

        # 3. 白名单 + 安全过滤
        filter_reason = ""
        if action == "no_action":
            self._log("no_action", summary=summary, status="no_action",
                      reason=action_json.get("reason", ""))
            return {"ok": True, "action": "no_action", "reason": action_json.get("reason", "")}

        if action not in ALLOWED_ACTIONS:
            filter_reason = f"行动 {action} 不在白名单"
            self._log(action, summary=summary, status="filtered", filter_reason=filter_reason,
                      room=str(action_json.get("room", "")),
                      text=str(action_json.get("text", "")),
                      reason=str(action_json.get("reason", "")))
            return {"ok": False, "action": "filtered", "reason": filter_reason}

        confidence = float(action_json.get("confidence") or 0.0)
        if confidence < self.cfg.confidence_threshold:
            filter_reason = f"置信度 {confidence:.2f} < 阈值 {self.cfg.confidence_threshold}"
            self._log(action, summary=summary, status="filtered", filter_reason=filter_reason,
                      room=str(action_json.get("room", "")),
                      member=str(action_json.get("member", "")),
                      text=str(action_json.get("text", "")),
                      reason=str(action_json.get("reason", "")), confidence=confidence)
            return {"ok": False, "action": "filtered", "reason": filter_reason}

        # 4. 夜间规则：23:00-07:00 只允许 notify（用家庭时区，容器可能跑在 UTC）
        # （now_hour 仅供测试/调试注入，生产走当前家庭时间）
        beijing_hour = house_now().hour
        try:
            hour = int(payload.get("now_hour")) if payload.get("now_hour") is not None else beijing_hour
        except (ValueError, TypeError):
            hour = beijing_hour
        night = hour >= self.cfg.night_start or hour < self.cfg.night_end
        if night and action != "notify":
            filter_reason = f"夜间（{self.cfg.night_start}:00-{self.cfg.night_end}:00）只允许 notify，拒绝 {action}"
            self._log(action, summary=summary, status="filtered", filter_reason=filter_reason,
                      room=str(action_json.get("room", "")),
                      member=str(action_json.get("member", "")),
                      text=str(action_json.get("text", "")),
                      reason=str(action_json.get("reason", "")), confidence=confidence)
            return {"ok": False, "action": "filtered", "reason": filter_reason}

        # 5. 冷却：同类行动（action+room+member）30 分钟内不重复
        cooldown_ts = time.time() - self.cfg.cooldown_minutes * 60
        try:
            from butler.store import repo
            recent = repo.recent_decision_since(
                action, cooldown_ts,
                room=str(action_json.get("room", "")),
                member=str(action_json.get("member", "")),
            )
        except Exception as e:
            logger.warning("cooldown check failed: %s", e)
            recent = []
        if recent:
            last_ts = recent[0].get("ts", 0)
            mins_ago = (time.time() - last_ts) / 60
            filter_reason = f"同类行动 {action} 在 {mins_ago:.0f} 分钟前执行过（冷却 {self.cfg.cooldown_minutes} 分钟）"
            self._log(action, summary=summary, status="filtered", filter_reason=filter_reason,
                      room=str(action_json.get("room", "")),
                      member=str(action_json.get("member", "")),
                      text=str(action_json.get("text", "")),
                      reason=str(action_json.get("reason", "")), confidence=confidence)
            return {"ok": False, "action": "filtered", "reason": filter_reason}

        # 6. 执行
        result = await self.router.execute(action_json)
        self._log(action, summary=summary, status="ok",
                  room=str(action_json.get("room", "")),
                  member=str(action_json.get("member", "")),
                  text=str(action_json.get("text", "")),
                  reason=str(action_json.get("reason", "")),
                  confidence=confidence)
        logger.info("decision executed action=%s result=%s (%.1fs)", action, result, time.time() - start)
        return {
            "ok": True, "action": action,
            "room": str(action_json.get("room", "")),
            "member": str(action_json.get("member", "")),
            "text": str(action_json.get("text", "")),
            "result": result,
            "confidence": confidence,
            "elapsed_ms": int((time.time() - start) * 1000),
        }

    # ---- LLM 推理 ----

    async def _reason(self, summary: str):
        """调用 LLM 返回 (action_json, error)。action_json=None 表示失败。"""
        llm = getattr(self.rt, "llm", None)
        if llm is None:
            return None, "llm not ready"
        try:
            text, _ = await llm.chat(
                _SYSTEM,
                [{"role": "user", "content": f"当前家庭状态快照：\n{summary}\n\n请输出决策 JSON："}],
                max_tokens=600,
                temperature=0.2,
            )
        except Exception as e:
            logger.warning("decision LLM call failed: %s", e)
            return None, f"LLM 调用失败：{e}"

        raw = (text or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                try:
                    obj = json.loads(m.group())
                except json.JSONDecodeError:
                    return None, f"LLM 输出不是有效 JSON：{raw[:200]}"
            else:
                return None, f"LLM 输出不是有效 JSON：{raw[:200]}"
        if not isinstance(obj, dict):
            return None, f"LLM 输出不是对象：{raw[:200]}"
        return obj, ""

    # ---- 落库 ----

    def _log(self, action: str, *, summary: str = "", status: str = "ok",
             room: str = "", member: str = "", text: str = "", reason: str = "",
             confidence: float = 0.0, filter_reason: str = "") -> None:
        try:
            from butler.store import repo
            repo.add_decision_run(
                action, summary=summary[:2000], room=room, member=member,
                text=text, reason=reason, confidence=confidence,
                status=status, filter_reason=filter_reason,
            )
        except Exception as e:
            logger.warning("add_decision_run failed: %s", e)
