"""记忆投喂引擎：把审核通过的事实通过 doubao2api 发到对应角色的豆包APP对话。

设计：
- 不自动投喂，只由 WebUI 手动触发
- 按角色批量打包投喂：把该角色所有待投喂事实拼成一条消息发出去
- 投喂成功 → feed_status=fed，记录 fed_ts + fed_content 快照
- 投喂失败 → feed_status=failed，记录 feed_error
- 已投喂的事实如果内容被改 → feed_status=changed（提醒用户去手机APP删旧版再重新投）
- conversation_id 失效/未绑定 → 明确报错，WebUI 提供重绑定入口
"""
from __future__ import annotations

import json
import os
import time

from butler.core.atomic_json import write_json_atomic
from butler.logging_setup import get_logger

logger = get_logger("butler.memory.feeder")

# 参与投喂的 6 个角色（xiaoai/xiaotiancai 是壳角色不投）
FEEDABLE_ROLES = ["butler", "jarvis", "caesar", "luna", "gu_anheng", "xiaoyue"]

ROLE_NAMES = {
    "butler": "豆包管家",
    "jarvis": "贾维斯",
    "caesar": "凯撒",
    "luna": "露娜",
    "gu_anheng": "顾安恒",
    "xiaoyue": "晓月",
}


class MemoryFeeder:
    def __init__(self, rt):
        self.rt = rt

    # ---------- conversation_id 管理 ----------

    def _role_state_path(self) -> str:
        return os.path.join(self.rt.settings.data_dir, "role_state.json")

    def _read_role_state(self) -> dict:
        path = self._role_state_path()
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except Exception as e:
            # 表行 109 M-09：只捕 FileNotFoundError，半截 JSON 会把异常抛穿成投喂接口全线 500
            logger.error("role_state.json unreadable, treat as empty: %s", e)
            return {}

    def _write_role_state(self, state: dict) -> None:
        # 表行 109 M-09：open(path,"w") + json.dump 先把文件截空再逐块写，崩在中途＝半截 JSON 落在盘上
        write_json_atomic(self._role_state_path(), state)

    def get_conversation_id(self, role_id: str) -> str | None:
        st = self._read_role_state()
        entry = st.get(role_id) or {}
        cid = entry.get("conversation_id")
        return cid if cid else None

    def bind_conversation(self, role_id: str, conversation_id: str) -> None:
        """重新绑定某角色的对话 ID。"""
        if not role_id or not conversation_id:
            raise ValueError("role_id and conversation_id required")
        st = self._read_role_state()
        st[role_id] = {
            "conversation_id": conversation_id,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._write_role_state(st)
        logger.info("rebound role %s -> conversation %s", role_id, conversation_id)

    # ---------- 投喂 ----------

    async def feed_role(self, role_id: str) -> dict:
        """把某角色所有待投喂事实打包发到对应对话。"""
        from butler.store import repo

        cid = self.get_conversation_id(role_id)
        if not cid:
            return {"ok": False, "error": "no_conversation",
                    "message": f"角色 {ROLE_NAMES.get(role_id, role_id)} 未绑定对话，请先在豆包APP打开对应对话，把 conversation_id 粘进来"}

        # 查待投喂事实：approved 且 feed_status in (pending, failed, changed)
        pending = repo.list_facts(status="approved", limit=500)
        pending = [f for f in pending if f.get("target_role") == role_id
                   and f.get("feed_status") in ("pending", "failed", "changed")]
        if not pending:
            return {"ok": True, "fed": 0, "skipped": 0, "message": "没有待投喂的事实"}

        # 拼消息——用"请记住"关键词触发豆包显式记忆
        lines = ["请记住以下关于我的事实，以后对话中自然运用："]
        for f in pending:
            lines.append(f"· {f['content']}")
        text = "\n".join(lines)

        # 调 doubao2api，发到指定对话
        try:
            reply, new_cid = await self.rt.doubao.chat(
                text,
                keep_conversation=True,
                conversation_id=cid,
                silent=True,
            )
        except Exception as e:
            logger.error("feed_role %s request failed: %s", role_id, e)
            for f in pending:
                repo.mark_fact_failed(f["id"], str(e))
            return {"ok": False, "error": "request_failed", "message": str(e)}

        # 检查是否真的成功（doubao2api 错误时 reply 可能是"对话失败：..."）
        if reply.startswith("对话失败"):
            for f in pending:
                repo.mark_fact_failed(f["id"], reply)
            return {"ok": False, "error": "doubao_error", "message": reply}

        # 成功：标记所有事实为 fed
        now = time.time()
        for f in pending:
            repo.mark_fact_fed(f["id"], now)
        logger.info("feed_role %s: %d facts fed to conversation %s",
                    role_id, len(pending), cid)
        return {"ok": True, "fed": len(pending), "skipped": 0,
                "conversation_id": cid, "reply": reply[:200]}

    async def feed_fact(self, fact_id: int) -> dict:
        """投喂单条事实（用于重新投喂已变更的）。"""
        from butler.store import repo
        fact = repo.get_fact(fact_id)
        if not fact:
            return {"ok": False, "error": "not_found"}
        if fact["status"] != "approved":
            return {"ok": False, "error": "not_approved",
                    "message": "事实尚未审核通过"}
        role_id = fact.get("target_role") or "butler"
        cid = self.get_conversation_id(role_id)
        if not cid:
            return {"ok": False, "error": "no_conversation",
                    "message": f"角色 {ROLE_NAMES.get(role_id, role_id)} 未绑定对话"}

        text = f"请记住：{fact['content']}"
        try:
            reply, _ = await self.rt.doubao.chat(
                text, keep_conversation=True, conversation_id=cid, silent=True,
            )
        except Exception as e:
            repo.mark_fact_failed(fact_id, str(e))
            return {"ok": False, "error": "request_failed", "message": str(e)}

        if reply.startswith("对话失败"):
            repo.mark_fact_failed(fact_id, reply)
            return {"ok": False, "error": "doubao_error", "message": reply}

        repo.mark_fact_fed(fact_id, time.time())
        return {"ok": True, "fed": 1, "conversation_id": cid}

    # ---------- 概览（WebUI 用） ----------

    def overview(self) -> dict:
        """各角色投喂概况。"""
        from butler.store import repo
        state = self._read_role_state()
        result = {}
        for rid in FEEDABLE_ROLES:
            entry = state.get(rid) or {}
            cid = entry.get("conversation_id")
            # 待投喂：approved 且 feed_status in (pending, failed, changed)
            all_approved = repo.list_facts(status="approved", limit=1000)
            mine = [f for f in all_approved if f.get("target_role") == rid]
            pending_feed = [f for f in mine if f.get("feed_status") in ("pending", "failed", "changed")]
            fed = [f for f in mine if f.get("feed_status") == "fed"]
            changed = [f for f in mine if f.get("feed_status") == "changed"]
            failed = [f for f in mine if f.get("feed_status") == "failed"]
            result[rid] = {
                "name": ROLE_NAMES.get(rid, rid),
                "conversation_id": cid,
                "bound": bool(cid),
                "pending_count": len(pending_feed),
                "fed_count": len(fed),
                "changed_count": len(changed),
                "failed_count": len(failed),
                "last_error": failed[0].get("feed_error", "") if failed else "",
                "facts": {
                    "pending": pending_feed,
                    "fed": fed,
                    "changed": changed,
                },
            }
        return result

    # ---------- 对账（问豆包它记得什么，和我们的库比对） ----------

    async def reconcile_role(self, role_id: str) -> dict:
        """问豆包它记得什么，和我们库里的事实比对。返回对账结果。"""
        from butler.store import repo

        cid = self.get_conversation_id(role_id)
        if not cid:
            return {"ok": False, "error": "no_conversation",
                    "message": f"角色 {ROLE_NAMES.get(role_id, role_id)} 未绑定对话"}

        # 1. 问豆包它记得什么
        ask_prompt = (
            f"请列出你目前记住的关于这个家庭/这个人的所有信息，"
            f"包括偏好、习惯、家庭成员、重要日期、饮食喜好。"
            f"如果记不清就说记不清，不要编造。逐条列出。"
        )
        try:
            reply, _ = await self.rt.doubao.chat(
                ask_prompt, keep_conversation=True, conversation_id=cid, silent=True,
            )
        except Exception as e:
            return {"ok": False, "error": "request_failed", "message": str(e)}

        if reply.startswith("对话失败"):
            return {"ok": False, "error": "doubao_error", "message": reply}

        # 2. 我们库里这个角色的事实
        our_facts = repo.list_facts(status="approved", limit=500)
        our_facts = [f for f in our_facts if f.get("target_role") == role_id]
        our_text = "\n".join(f"- {f['content']}" for f in our_facts) or "（无）"

        # 3. LLM 比对
        compare_system = (
            "你是记忆对账助手。比对两边的家庭事实：\n"
            "A=我们系统记录的（经过人工审核）\n"
            "B=豆包对话里记住的\n"
            "分类输出：\n"
            "- confirmed: 两边一致或B能正确说出A的内容\n"
            "- missing: A有B没有（豆包忘了/不知道）\n"
            "- hallucinated: B有A没有（豆包自己学到的或幻觉）\n"
            "- conflicting: 两边说的不一致（豆包记错了）\n"
            "严格输出JSON：\n"
            '{"confirmed": ["..."], "missing": ["..."], "hallucinated": ["..."], "conflicting": [{"our": "...", "doubao": "..."}]}'
        )
        compare_user = f"【我们的记录A】\n{our_text}\n\n【豆包记住的B】\n{reply}"

        try:
            cmp_text, _ = await self.rt.llm.chat(
                compare_system,
                [{"role": "user", "content": compare_user}],
                max_tokens=1500,
                temperature=0.1,
                backend="new_api",
            )
        except Exception as e:
            return {"ok": False, "error": "compare_failed", "message": str(e),
                    "doubao_recall": reply}

        # 解析比对结果
        result = {"confirmed": [], "missing": [], "hallucinated": [], "conflicting": []}
        cmp_obj = self._parse_json(cmp_text)
        if cmp_obj:
            result = {
                "confirmed": cmp_obj.get("confirmed") or [],
                "missing": cmp_obj.get("missing") or [],
                "hallucinated": cmp_obj.get("hallucinated") or [],
                "conflicting": cmp_obj.get("conflicting") or [],
            }

        result["doubao_recall"] = reply
        result["our_facts_count"] = len(our_facts)
        return {"ok": True, "role_id": role_id, **result}

    async def correct_role(self, role_id: str, wrong: str, right: str) -> dict:
        """发纠正消息：告诉豆包之前记错了，正确信息是什么。"""
        cid = self.get_conversation_id(role_id)
        if not cid:
            return {"ok": False, "error": "no_conversation"}
        correct_text = (
            f"【纠正记忆】之前你记住的「{wrong}」是不准确的。"
            f"正确的信息是：{right}。请更新你的记忆，以后以这个为准。"
        )
        try:
            reply, _ = await self.rt.doubao.chat(
                correct_text, keep_conversation=True, conversation_id=cid, silent=True,
            )
        except Exception as e:
            return {"ok": False, "error": "request_failed", "message": str(e)}
        return {"ok": True, "reply": reply[:200]}

    @staticmethod
    def _parse_json(text: str) -> dict:
        import json
        text = (text or "").strip()
        if text.startswith("```"):
            text = "\n".join(l for l in text.split("\n") if not l.strip().startswith("```"))
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
