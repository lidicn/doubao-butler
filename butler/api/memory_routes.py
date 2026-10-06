"""记忆路由：本地对话流水、语义记忆（memory-agent）、成员名单、防重复指纹、记忆事实审核。"""
from __future__ import annotations

import asyncio
import time

from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.logging_setup import get_logger
from butler.runtime import get_runtime
from butler.store import repo

logger = get_logger("butler.memory_routes")


# ---------- 原有：本地对话流水 / 语义记忆 / 成员 / 指纹 ----------

async def local_list(request):
    g = guard(request)
    if g:
        return g
    limit = int(request.query_params.get("limit", "100"))
    turns = await asyncio.to_thread(repo.recent_turns, limit)
    return ok({"turns": turns})


async def local_delete(request):
    g = guard(request)
    if g:
        return g
    tid = request.path_params.get("id")
    ok_del = await asyncio.to_thread(repo.delete_turn, int(tid))
    return ok({"deleted": ok_del})


async def semantic_list(request):
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    member = request.query_params.get("member", "")
    memories = await rt.memory.list_memories(member)
    return ok({"memories": memories})


async def semantic_add(request):
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    content = str(body.get("content", "")).strip()
    source_refs = body.get("source_refs") or []
    member = str(body.get("member", ""))
    if not content:
        return err("content required")
    if not isinstance(source_refs, list) or not source_refs:
        return err("source_refs 必填（event:/insight:/activity: 前缀）")
    rt = get_runtime()
    r = await rt.memory.add_memory(content, source_refs, member)
    return ok(r)


async def members(request):
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    ms = await rt.memory.get_members()
    return ok({"members": ms})


async def fingerprints(request):
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    s = rt.settings
    since = time.time() - s.dedup_window_days * 86400
    from butler.store.db import get_conn

    c = get_conn()
    rows = c.execute(
        "SELECT member, COUNT(*) AS n FROM dedup_fingerprints WHERE ts>=? GROUP BY member", (since,)
    ).fetchall()
    return ok({"window_days": s.dedup_window_days, "threshold": s.dedup_jaccard_threshold, "by_member": {r["member"]: r["n"] for r in rows}})


# ---------- v1.1：记忆事实审核（memory_facts） ----------

async def facts_list(request):
    g = guard(request)
    if g:
        return g
    """GET /api/memory/facts?status=pending&fact_type=preference&limit=100"""
    status = request.query_params.get("status", "")
    ftype = request.query_params.get("fact_type", "")
    limit = int(request.query_params.get("limit", "100"))
    facts = await asyncio.to_thread(repo.list_facts, status, ftype, limit)
    counts = await asyncio.to_thread(repo.count_facts_by_status)
    return ok({"facts": facts, "counts": counts})


async def facts_detail(request):
    g = guard(request)
    if g:
        return g
    fid = int(request.path_params.get("id", "0"))
    fact = await asyncio.to_thread(repo.get_fact, fid)
    if not fact:
        return err("fact not found", 404)
    return ok({"fact": fact})


async def facts_update(request):
    """PUT /api/memory/facts/{id}  编辑内容/类型/置信度"""
    g = guard(request)
    if g:
        return g
    fid = int(request.path_params.get("id", "0"))
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    content = str(body.get("content", "")).strip()
    ftype = str(body.get("fact_type", "")).strip()
    confidence = body.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            return err("confidence must be a number")
    updated = await asyncio.to_thread(
        repo.update_fact, fid,
        content=content, fact_type=ftype, confidence=confidence,
    )
    if not updated:
        return err("fact not found or no change", 404)
    return ok({"updated": True})


async def facts_approve(request):
    """POST /api/memory/facts/{id}/approve"""
    g = guard(request)
    if g:
        return g
    fid = int(request.path_params.get("id", "0"))
    ok_flag = await asyncio.to_thread(repo.set_fact_status, fid, "approved")
    if not ok_flag:
        return err("fact not found", 404)
    return ok({"approved": True})


async def facts_reject(request):
    """POST /api/memory/facts/{id}/reject"""
    g = guard(request)
    if g:
        return g
    fid = int(request.path_params.get("id", "0"))
    ok_flag = await asyncio.to_thread(repo.set_fact_status, fid, "rejected")
    if not ok_flag:
        return err("fact not found", 404)
    return ok({"rejected": True})


async def facts_delete(request):
    """DELETE /api/memory/facts/{id}"""
    g = guard(request)
    if g:
        return g
    fid = int(request.path_params.get("id", "0"))
    ok_flag = await asyncio.to_thread(repo.delete_fact, fid)
    if not ok_flag:
        return err("fact not found", 404)
    return ok({"deleted": True})


async def facts_bulk_approve(request):
    """POST /api/memory/facts/bulk-approve  批量批准事实（按最低置信度）。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        body = {}
    min_conf = float(body.get("min_conf", 0.7))

    from butler.store.db import get_conn
    conn = get_conn()
    cur = conn.execute(
        "UPDATE memory_facts SET status = 'approved' WHERE status = 'pending' AND confidence >= ?",
        (min_conf,)
    )
    conn.commit()
    return ok({"approved": cur.rowcount, "min_conf": min_conf})


async def facts_bulk_reject(request):
    """POST /api/memory/facts/bulk-reject  批量拒绝事实（按最高置信度）。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        body = {}
    max_conf = float(body.get("max_conf", 0.6))

    from butler.store.db import get_conn
    conn = get_conn()
    cur = conn.execute(
        "UPDATE memory_facts SET status = 'rejected' WHERE status = 'pending' AND confidence <= ?",
        (max_conf,)
    )
    conn.commit()
    return ok({"rejected": cur.rowcount, "max_conf": max_conf})


# ---------- v1.1：手动触发记忆提取（调试/补跑） ----------

async def extract_run(request):
    """POST /api/memory/extract/run  立即跑一次记忆提取（默认最近7天）。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        body = {}
    days = int(body.get("days", 7) or 7)
    days = max(1, min(days, 90))  # 上限 90 天，防误传
    rt = get_runtime()
    from butler.memory.extractor import MemoryExtractor
    extractor = MemoryExtractor(rt)
    result = await extractor.run(days=days)
    logger.info("manual memory extract: %s", result)
    if result.get("error"):
        return err(f"提取失败：{result['error']}", 502)
    return ok(result)



# ---------- v1.1.1 投喂（手动触发） ----------

async def feed_overview(request):
    g = guard(request)
    if g:
        return g
    """GET /api/memory/feed/overview  各角色投喂概览。"""
    rt = get_runtime()
    from butler.memory.feeder import MemoryFeeder
    feeder = MemoryFeeder(rt)
    ov = await asyncio.to_thread(feeder.overview)
    return ok({"overview": ov})


async def feed_role(request):
    """POST /api/memory/feed/{role_id}  投喂某角色所有待投喂事实。
    闸门：必须传 body.force=true，否则返回 403 警告。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not body.get("force"):
        return err("投喂需要用户在 WebUI 确认。API 调用请传 body.force=true", 403)
    role_id = request.path_params.get("role_id", "")
    rt = get_runtime()
    from butler.memory.feeder import MemoryFeeder
    feeder = MemoryFeeder(rt)
    result = await feeder.feed_role(role_id)
    logger.info("feed_role %s: %s", role_id, result)
    return ok(result)


async def feed_fact(request):
    """POST /api/memory/facts/{id}/feed  投喂单条事实。"""
    g = guard(request)
    if g:
        return g
    fid = int(request.path_params.get("id", "0"))
    rt = get_runtime()
    from butler.memory.feeder import MemoryFeeder
    feeder = MemoryFeeder(rt)
    result = await feeder.feed_fact(fid)
    return ok(result)


async def feed_bind(request):
    """POST /api/memory/feed/bind  {"role_id": "jarvis", "conversation_id": "xxx"}  重绑定对话。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    role_id = str(body.get("role_id", "")).strip()
    cid = str(body.get("conversation_id", "")).strip()
    if not role_id or not cid:
        return err("role_id and conversation_id required")
    rt = get_runtime()
    from butler.memory.feeder import MemoryFeeder
    feeder = MemoryFeeder(rt)
    try:
        feeder.bind_conversation(role_id, cid)
    except ValueError as e:
        return err(str(e))
    return ok({"bound": True, "role_id": role_id, "conversation_id": cid})


async def fact_set_role(request):
    """PUT /api/memory/facts/{id}/role  {"target_role": "jarvis"}  改事实归属。"""
    g = guard(request)
    if g:
        return g
    fid = int(request.path_params.get("id", "0"))
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    role = str(body.get("target_role", "")).strip()
    if role not in ("butler", "jarvis", "caesar", "luna", "gu_anheng", "xiaoyue"):
        return err("invalid target_role")
    from butler.store.db import get_conn
    c = get_conn()
    cur = c.execute("UPDATE memory_facts SET target_role=? WHERE id=?", (role, fid))
    c.commit()
    if cur.rowcount == 0:
        return err("fact not found", 404)
    return ok({"updated": True})


# ---------- v1.1.2 对账 ----------

async def reconcile_role(request):
    """POST /api/memory/reconcile/{role_id}  问豆包它记得什么，和我们的库比对。"""
    g = guard(request)
    if g:
        return g
    role_id = request.path_params.get("role_id", "")
    rt = get_runtime()
    from butler.memory.feeder import MemoryFeeder
    feeder = MemoryFeeder(rt)
    result = await feeder.reconcile_role(role_id)
    return ok(result)


async def correct_role(request):
    """POST /api/memory/correct/{role_id}  {"wrong": "...", "right": "..."}  纠正豆包记忆。"""
    g = guard(request)
    if g:
        return g
    role_id = request.path_params.get("role_id", "")
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    wrong = str(body.get("wrong", "")).strip()
    right = str(body.get("right", "")).strip()
    if not wrong or not right:
        return err("wrong and right required")
    rt = get_runtime()
    from butler.memory.feeder import MemoryFeeder
    feeder = MemoryFeeder(rt)
    result = await feeder.correct_role(role_id, wrong, right)
    return ok(result)


# ---------- 一键投喂设备说明书 ----------

DEVICE_MANUAL = """请记住，你是我家的智能家居管家。你不是被动的工具——你要根据我说的话，主动判断我想做什么，然后帮我操控设备。

比如我说"好热"，你自己判断该开空调、设制冷、调低温；我说"光线太暗了"，你自己判断该开哪盏灯；我说"该睡觉了"，你自己判断该关哪些设备。不要等我精确说出设备名和操作，你要理解我的意图。

你可以控制以下设备：

【书房】显示器挂灯（打开/关闭）、书房空调（打开/关闭/制冷/制热/送风/调温度16-30度）
【客厅】客厅主灯（打开/关闭）、客厅电视（打开/关闭/换台）、客厅风扇（打开/关闭）
【主卧室】主卧空调（打开/关闭/调温度）、床头灯（打开/关闭）

控制设备时用这个格式回复：
好的！【设备：设备名】【操作：操作】
有温度就加【温度：26】，有模式就加【模式：制冷】，换台就加【频道：CCTV1】

操作只能用：打开、关闭、换台、设定温度、设模式、设风速。温度用单独的【温度：XX】标签，不要写在操作里。

如果我说"看电影"或"我要睡觉"或"我出门了"，直接回复：
好的！【场景：观影模式】
好的！【场景：睡眠模式】
好的！【场景：离家模式】

如果我问"书房空调几度"或"客厅灯开了吗"，回复：
好的！【查询：书房空调】
好的！【查询：客厅主灯】

如果我只是聊天，正常回复就行，不要加【】。不要编造不在清单里的设备。

请确认你已记住，并简要复述你有哪些设备可以执行。"""


async def feed_device_manual(request):
    """POST /api/memory/feed-device-manual  一键把设备说明书投喂到豆包管家对话。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    from butler.memory.feeder import MemoryFeeder
    feeder = MemoryFeeder(rt)
    cid = feeder.get_conversation_id("butler")
    if not cid:
        return err("豆包管家未绑定对话")
    try:
        reply, _ = await rt.doubao.chat(
            DEVICE_MANUAL, keep_conversation=True, conversation_id=cid, silent=True,
        )
    except Exception as e:
        return err(f"投喂失败: {e}")
    return ok({"ok": True, "reply": reply[:300]})


async def feed_history(request):
    """GET /api/memory/feed_history  所有已投喂事实（按时间倒序）。"""
    g = guard(request)
    if g:
        return g
    all_facts = repo.list_facts(status="approved", limit=1000)
    fed = [f for f in all_facts if f.get("feed_status") == "fed"]
    fed.sort(key=lambda x: x.get("fed_ts") or 0, reverse=True)
    return ok({"count": len(fed), "facts": fed})


def routes():
    return [
        Route("/api/memory/local", local_list, methods=["GET"]),
        Route("/api/memory/local/{id:int}", local_delete, methods=["DELETE"]),
        Route("/api/memory/semantic", semantic_list, methods=["GET"]),
        Route("/api/memory/semantic", semantic_add, methods=["POST"]),
        Route("/api/memory/members", members, methods=["GET"]),
        Route("/api/memory/fingerprints", fingerprints, methods=["GET"]),
        # v1.1 记忆事实
        Route("/api/memory/facts", facts_list, methods=["GET"]),
        Route("/api/memory/facts/extract/run", extract_run, methods=["POST"]),
        Route("/api/memory/facts/{id:int}", facts_detail, methods=["GET"]),
        Route("/api/memory/facts/{id:int}", facts_update, methods=["PUT"]),
        Route("/api/memory/facts/{id:int}", facts_delete, methods=["DELETE"]),
        Route("/api/memory/facts/{id:int}/approve", facts_approve, methods=["POST"]),
        Route("/api/memory/facts/{id:int}/reject", facts_reject, methods=["POST"]),
        Route("/api/memory/facts/bulk-approve", facts_bulk_approve, methods=["POST"]),
        Route("/api/memory/facts/bulk-reject", facts_bulk_reject, methods=["POST"]),
        # v1.1.1 投喂
        Route("/api/memory/feed/overview", feed_overview, methods=["GET"]),
        Route("/api/memory/feed/bind", feed_bind, methods=["POST"]),
        Route("/api/memory/feed/{role_id}", feed_role, methods=["POST"]),
        Route("/api/memory/facts/{id:int}/feed", feed_fact, methods=["POST"]),
        Route("/api/memory/facts/{id:int}/role", fact_set_role, methods=["PUT"]),
        # v1.1.2 对账
        Route("/api/memory/reconcile/{role_id}", reconcile_role, methods=["POST"]),
        Route("/api/memory/correct/{role_id}", correct_role, methods=["POST"]),
        Route("/api/memory/feed-device-manual", feed_device_manual, methods=["POST"]),
        Route("/api/memory/feed_history", feed_history, methods=["GET"]),
    ]
