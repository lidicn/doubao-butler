"""记忆提取引擎：每天凌晨分析对话 + MA 作息数据，自动提取偏好/家庭/事件/习惯到 memory_facts。

工作流：
1. 从 chat_logs 拉最近 N 天对话
2. 从 memory-agent 拉成员作息基线（best-effort）
3. 组装 prompt 给 LLM（new_api，低温稳定 JSON）
4. 解析返回，去重后写入 memory_facts（status=pending，等待用户审核）

设计原则：
- 低置信度不丢弃，全部存为 pending，由 WebUI 审核决定
- 去重只做同类型 content 完全匹配 / 包含匹配，不做语义去重（避免误删）
- MA 数据拉取失败不阻塞，只用对话数据也能跑
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from butler.logging_setup import get_logger

logger = get_logger("butler.memory.extractor")


# 提取 prompt：要求 LLM 稳定输出 JSON，不猜测，只提取有依据的事实
EXTRACT_SYSTEM_PROMPT = """你是一个家庭记忆分析助手。分析家庭成员与管家的最近对话，提取值得长期记住的事实。

只提取以下四类：
1. preference（偏好）：用户明确表达的喜好、厌恶、说话风格倾向
2. family（家庭）：家庭成员的年龄、爱好、关系等新信息
3. event（事件）：重要且有时限的事（如复诊、考试、出差、聚会）
4. habit（习惯）：重复出现的行为模式（如"早上7点看CCTV1"）

铁律：
- 只提取有明确对话依据的事实，绝不猜测、不外推
- content 要简短一句话，能直接塞进对话上下文
- confidence 表示你对这条事实的把握（0~1）：明确说出=0.9，暗示/需推断=0.6
- 闲聊、设备控制指令、一次性请求、测试消息一律忽略
- 某类没有可提取信息就返回空数组，不要凑数

每条事实必须标注 target_role（这条记忆该喂给哪个角色）：
- butler：家庭通用事实（全家都该知道的，如"住在深圳"、"有智能家居"）
- jarvis：理叔（老板）个人事实（他的工作习惯、偏好、书房相关）
- caesar：凯文相关事实（他的爱好、学校、日程）
- luna：爱美丽相关事实（她的爱好、学校、日程）
- xiaoyue：饮食/健康相关事实（口味、忌口、身体状况）
- gu_anheng：安防相关事实（家庭成员作息、异常行为）

默认用 butler。只在明确归属某人/某领域时才改。

严格输出 JSON，不要输出任何其他内容（不要 markdown、不要解释）：
{
  "preferences": [{"content": "...", "confidence": 0.9, "target_role": "butler"}],
  "family": [{"content": "...", "confidence": 0.8, "target_role": "butler"}],
  "events": [{"content": "...", "date_hint": "MM-DD 或 HH:MM", "confidence": 0.7, "target_role": "jarvis"}],
  "habits": [{"content": "...", "confidence": 0.8, "target_role": "butler"}]
}
"""


class MemoryExtractor:
    """每日记忆提取器。"""

    def __init__(self, rt):
        self.rt = rt

    async def run(self, days: int = 7, max_turns: int = 300) -> dict:
        """执行一次提取。返回 {extracted, skipped_dup, facts, error?}。"""
        dialogs = self._fetch_recent_dialogs(days, max_turns)
        if not dialogs:
            logger.info("memory extract: no dialogs in last %d days", days)
            return {"extracted": 0, "skipped_dup": 0, "facts": []}

        schedules = await self._fetch_ma_schedules()
        user_prompt = self._build_prompt(dialogs, schedules)

        try:
            text, _ = await self.rt.llm.chat(
                EXTRACT_SYSTEM_PROMPT,
                [{"role": "user", "content": user_prompt}],
                max_tokens=2000,
                temperature=0.2,
                backend="new_api",
            )
        except Exception as e:
            logger.error("memory extract LLM call failed: %s", e)
            return {"extracted": 0, "skipped_dup": 0, "facts": [], "error": str(e)}

        facts_raw = self._parse_llm_json(text)
        if not facts_raw:
            logger.warning("memory extract: LLM unparseable, raw=%s", text[:300])
            return {"extracted": 0, "skipped_dup": 0, "facts": [], "error": "unparseable_json"}

        inserted, skipped = self._dedupe_and_save(facts_raw, days)
        logger.info("memory extract done: %d inserted, %d dup-skipped (from %d dialogs)",
                    inserted, skipped, len(dialogs))
        return {"extracted": inserted, "skipped_dup": skipped, "facts": facts_raw}

    # ---------- 数据拉取 ----------

    def _fetch_recent_dialogs(self, days: int, max_turns: int) -> list[dict]:
        """从 chat_logs 拉最近 N 天对话，过滤自动化推送噪音。

        限额截的是**最新**一截（DCD 裁定①：旧写法「ts 正序 ＋ LIMIT」切走的是最旧一截，
        提取会永远在学陈年旧事），取满后翻回升序再喂模型。
        """
        from butler.store.db import get_conn
        since = time.time() - days * 86400
        conn = get_conn()
        rows = conn.execute(
            """SELECT ts, user_msg, assistant_reply, role, source, room
               FROM chat_logs
               WHERE ts >= ? AND user_msg != ''
               ORDER BY ts DESC
               LIMIT ?""",
            (since, max_turns * 3),  # 多拉一些再过滤
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            msg = (d.get("user_msg") or "").strip()
            # 过滤自动化推送噪音
            if msg.startswith("请原样输出"):
                continue
            if "【ℹ️】" in msg:
                continue
            if msg.startswith("[{") or msg.startswith("[{\"block_type"):
                continue
            if "请记住以下关于我的事实" in msg:
                continue
            if "设备说明书" in msg and len(msg) > 200:
                continue
            # 太短的测试消息跳过
            if len(msg) < 3:
                continue
            result.append(d)
            if len(result) >= max_turns:
                break
        logger.info("extract: fetched %d raw rows, filtered to %d real dialogs",
                    len(rows), len(result))
        # DCD 裁定①：SQL 倒序拿到的最新一截在这里翻回升序，⛔ 把倒序直接送进 prompt
        result.reverse()
        return result

    async def _fetch_ma_schedules(self) -> dict:
        """拉 MA 成员作息基线（best-effort，失败返回空）。"""
        try:
            members = await self.rt.memory.get_members()
        except Exception as e:
            logger.debug("MA get_members failed: %s", e)
            return {}
        schedules: dict[str, dict] = {}
        for m in (members or [])[:10]:
            if not isinstance(m, dict):
                continue
            name = (m.get("name") or "").strip()
            if not name:
                continue
            try:
                sched = await self.rt.memory.member_schedule(name, days=14)
            except Exception as e:
                logger.debug("MA member_schedule(%s) failed: %s", name, e)
                continue
            if isinstance(sched, dict) and sched:
                schedules[name] = sched
        return schedules

    # ---------- Prompt 组装 ----------

    def _build_prompt(self, dialogs: list[dict], schedules: dict) -> str:
        lines: list[str] = []
        for d in dialogs:
            ts_str = datetime.fromtimestamp(d["ts"]).strftime("%m-%d %H:%M")
            room = d.get("room") or ""
            user_msg = (d.get("user_msg") or "").strip()
            assistant = (d.get("assistant_reply") or "").strip()
            if not user_msg:
                continue
            head = f"[{ts_str}]"
            if room:
                head += f"[{room}]"
            line = f"{head} 用户: {user_msg}"
            if assistant:
                if len(assistant) > 200:
                    assistant = assistant[:200] + "..."
                line += f"\n  管家: {assistant}"
            lines.append(line)

        dialog_text = "\n".join(lines)
        # 控制上下文长度：超过 8000 字符取尾部（最新的对话更重要）
        if len(dialog_text) > 8000:
            dialog_text = dialog_text[-8000:]

        sched_text = ""
        if schedules:
            sched_lines = []
            for name, s in schedules.items():
                first = s.get("median_first_seen") or s.get("typical_first_seen") or ""
                last = s.get("median_last_seen") or s.get("typical_last_seen") or ""
                parts = [f"{name}"]
                if first:
                    parts.append(f"平均首现 {first}")
                if last:
                    parts.append(f"末次 {last}")
                sched_lines.append("- " + "，".join(parts))
            if sched_lines:
                sched_text = (
                    "\n\n【家庭成员作息基线（来自视觉数据，仅供参考，不要直接当成新事实输出）】\n"
                    + "\n".join(sched_lines)
                )

        return (
            f"请分析以下最近 {len(dialogs)} 条对话记录，按 system 要求提取长期事实。"
            f"{sched_text}\n\n【对话记录】\n{dialog_text}"
        )

    # ---------- 解析与落库 ----------

    @staticmethod
    def _parse_llm_json(text: str) -> dict:
        """从 LLM 输出稳健提取 JSON 对象。"""
        text = (text or "").strip()
        if not text:
            return {}
        # 去 markdown 代码块
        if text.startswith("```"):
            cleaned = []
            for line in text.split("\n"):
                s = line.strip()
                if s.startswith("```"):
                    continue
                cleaned.append(line)
            text = "\n".join(cleaned).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def _dedupe_and_save(self, facts_raw: dict, days: int) -> tuple[int, int]:
        """去重后写入 memory_facts。返回 (inserted, skipped)。"""
        from butler.store.db import get_conn

        type_map = {
            "preferences": "preference",
            "family": "family",
            "events": "event",
            "habits": "habit",
        }
        candidates: list[dict] = []
        for key, ftype in type_map.items():
            items = facts_raw.get(key) or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                content = (item.get("content") or "").strip()
                if not content or len(content) < 3:
                    continue
                try:
                    conf = float(item.get("confidence", 0.5))
                except (TypeError, ValueError):
                    conf = 0.5
                conf = max(0.0, min(1.0, conf))
                target_role = str(item.get("target_role") or "butler").strip()
                if target_role not in ("butler", "jarvis", "caesar", "luna", "gu_anheng", "xiaoyue"):
                    target_role = "butler"
                candidates.append({
                    "fact_type": ftype,
                    "content": content,
                    "confidence": conf,
                    "target_role": target_role,
                    "meta": {
                        "date_hint": str(item.get("date_hint") or ""),
                        "window_days": days,
                    },
                })

        if not candidates:
            return 0, 0

        # 去重：查最近 30 天未拒绝的同类 fact
        conn = get_conn()
        since = time.time() - 30 * 86400
        existing = conn.execute(
            "SELECT content, fact_type FROM memory_facts WHERE ts >= ? AND status != 'rejected'",
            (since,),
        ).fetchall()
        existing_set = {(r["fact_type"], (r["content"] or "").strip()) for r in existing}

        inserted = 0
        skipped = 0
        for cand in candidates:
            ftype = cand["fact_type"]
            content = cand["content"]
            exact_key = (ftype, content)
            if exact_key in existing_set:
                skipped += 1
                continue
            # 包含式去重：同类已有 fact 完全包含新内容，或新内容包含已有 fact
            duplicate = False
            for (et, ec) in existing_set:
                if et == ftype and ec and (content in ec or ec in content):
                    duplicate = True
                    break
            if duplicate:
                skipped += 1
                continue

            # M5: 全部走 pending 待审核，不自动批准
            # （用户要求人工审核后再投喂，避免重复/不需要的事实直接进对话）
            conf = cand["confidence"]
            if conf < 0.6:
                skipped += 1
                continue  # 低置信度，忽略
            status = "pending"  # 全部待审核

            conn.execute(
                """INSERT INTO memory_facts (ts, fact_type, content, confidence, status, source, meta_json, target_role, feed_status)
                   VALUES (?, ?, ?, ?, ?, 'extraction', ?, ?, 'pending')""",
                (time.time(), ftype, content, conf, status,
                 json.dumps(cand["meta"], ensure_ascii=False),
                 cand["target_role"]),
            )
            existing_set.add(exact_key)
            inserted += 1

        conn.commit()
        return inserted, skipped
