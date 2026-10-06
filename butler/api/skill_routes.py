"""技能 API：CRUD / 试跑 / 运行历史 / 熔断状态 / 引擎清单。"""
from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.logging_setup import get_logger
from butler.runtime import get_runtime
from butler.skills.schema import validate_skill

logger = get_logger("butler.api.skills")


def _runner():
    rt = get_runtime()
    return getattr(rt, "runner", None)


async def skills_list(request: Request):
    g = guard(request)
    if g:
        return g
    runner = _runner()
    if runner is None:
        return err("runner not ready", 503)
    source = request.query_params.get("source", "")
    status = request.query_params.get("status", "")
    return ok({
        "items": runner.store.list(source=source, status=status),
        "stats": runner.store.stats(),
        "breaker": runner.breaker_status(),
        "engines": runner.registry.describe(),
    })


async def skills_save(request: Request):
    """创建/更新技能（按 id upsert）。"""
    g = guard(request)
    if g:
        return g
    runner = _runner()
    if runner is None:
        return err("runner not ready", 503)
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    try:
        normalized, verr = validate_skill(body)
        if verr:
            return err(verr)
    except Exception as e:
        return err(f"invalid skill: {e}")
    runner.store.save(normalized)
    return ok({"skill": normalized})


async def skills_delete(request: Request):
    g = guard(request)
    if g:
        return g
    runner = _runner()
    if runner is None:
        return err("runner not ready", 503)
    sid = request.path_params.get("skill_id", "")
    if not sid:
        return err("missing skill_id")
    return ok({"deleted": runner.store.delete(sid)})


async def skills_reload(request: Request):
    """热加载：重新从磁盘加载所有技能。"""
    g = guard(request)
    if g:
        return g
    runner = _runner()
    if runner is None:
        return err("runner not ready", 503)
    count = runner.store.reload()
    return ok({"reloaded": count, "stats": runner.store.stats()})


async def skill_set_status(request: Request):
    """修改技能状态：enabled/disabled/quarantined/draft。"""
    g = guard(request)
    if g:
        return g
    runner = _runner()
    if runner is None:
        return err("runner not ready", 503)
    sid = request.path_params.get("skill_id", "")
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    status = (body.get("status") or "").lower()
    if status not in ("enabled", "disabled", "quarantined", "draft"):
        return err("status must be one of: enabled, disabled, quarantined, draft")
    skill = runner.store.set_status(sid, status)
    if skill is None:
        return err("skill not found", 404)
    return ok({"skill": skill})


async def skill_quarantine(request: Request):
    """隔离技能（自动隔离的手动入口）。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    mgr = getattr(rt, "quarantine_mgr", None)
    if mgr is None:
        runner = _runner()
        if runner is None:
            return err("runner not ready", 503)
        sid = request.path_params.get("skill_id", "")
        try:
            body = await request.json()
        except Exception:
            body = {}
        reason = body.get("reason", "manual quarantine")
        skill = runner.store.quarantine(sid, reason)
        if skill is None:
            return err("skill not found", 404)
        return ok({"skill": skill})
    sid = request.path_params.get("skill_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = body.get("reason", "manual quarantine")
    ok_flag = mgr.quarantine(sid, reason)
    if not ok_flag:
        return err("skill not found or already quarantined", 404)
    skill = mgr.store.get(sid)
    return ok({"skill": skill})


async def skill_drafts_list(request: Request):
    """列出所有待确认的技能草稿。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    creator = getattr(rt, "skill_creator", None)
    if creator is None:
        return err("skill_creator not ready", 503)
    return ok({"drafts": creator.list_drafts(), "pending": dict(creator._pending)})


async def skill_draft_confirm(request: Request):
    """确认草稿，保存为 enabled。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    creator = getattr(rt, "skill_creator", None)
    if creator is None:
        return err("skill_creator not ready", 503)
    role_id = request.query_params.get("role", "butler")
    result = creator.confirm(role_id)
    if not result.get("ok"):
        return err(result.get("error", "confirm failed"))
    return ok({"skill": result["skill"]})


async def skill_draft_cancel(request: Request):
    """取消草稿。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    creator = getattr(rt, "skill_creator", None)
    if creator is None:
        return err("skill_creator not ready", 503)
    role_id = request.query_params.get("role", "butler")
    result = creator.cancel(role_id)
    if not result.get("ok"):
        return err(result.get("error", "cancel failed"))
    return ok({"message": result.get("message", "cancelled")})


async def quarantine_stats(request: Request):
    """隔离统计：列出所有被隔离的技能。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    mgr = getattr(rt, "quarantine_mgr", None)
    if mgr is None:
        return err("quarantine manager not ready", 503)
    return ok(mgr.get_stats())


async def quarantine_restore(request: Request):
    """恢复被隔离的技能（默认恢复为 disabled，需手动启用）。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    mgr = getattr(rt, "quarantine_mgr", None)
    if mgr is None:
        return err("quarantine manager not ready", 503)
    sid = request.path_params.get("skill_id", "")
    to_status = request.query_params.get("to", "disabled")
    if to_status not in ("disabled", "enabled"):
        return err("to must be disabled or enabled")
    ok_flag = mgr.restore(sid, to_status)
    if not ok_flag:
        return err("skill not found or not quarantined", 404)
    return ok({"restored": sid, "status": to_status})


async def quarantine_audit(request: Request):
    """查看技能隔离审计日志。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    mgr = getattr(rt, "quarantine_mgr", None)
    if mgr is None:
        return err("quarantine manager not ready", 503)
    sid = request.path_params.get("skill_id", "")
    limit = int(request.query_params.get("limit", "20"))
    return ok({"skill_id": sid, "entries": mgr.get_audit_log(sid, limit)})


async def quarantine_auto_recover(request: Request):
    """手动触发自动恢复检查（隔离超过24小时的技能恢复为 disabled）。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    mgr = getattr(rt, "quarantine_mgr", None)
    if mgr is None:
        return err("quarantine manager not ready", 503)
    recovered = mgr.auto_recover_check()
    return ok({"recovered": recovered})


async def skill_versions(request: Request):
    """列出技能的所有版本（按时间倒序）。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    runner = _runner()
    if runner is None:
        return err("runner not ready", 503)
    sid = request.path_params.get("skill_id", "")
    if not sid:
        return err("missing skill_id")
    if runner.store.get(sid) is None:
        return err("skill not found", 404)
    v_mgr = getattr(runner.store, "version_mgr", None)
    if v_mgr is None:
        return err("version manager not ready", 503)
    return ok({"skill_id": sid, "versions": v_mgr.list_versions(sid)})


async def skill_version_detail(request: Request):
    """获取指定版本的完整技能数据。"""
    g = guard(request)
    if g:
        return g
    runner = _runner()
    if runner is None:
        return err("runner not ready", 503)
    sid = request.path_params.get("skill_id", "")
    vid = request.path_params.get("version_id", "")
    v_mgr = getattr(runner.store, "version_mgr", None)
    if v_mgr is None:
        return err("version manager not ready", 503)
    data = v_mgr.get_version(sid, vid)
    if data is None:
        return err("version not found", 404)
    return ok({"skill_id": sid, "version": vid, "data": data})


async def skill_rollback(request: Request):
    """回滚到指定版本。"""
    g = guard(request)
    if g:
        return g
    runner = _runner()
    if runner is None:
        return err("runner not ready", 503)
    sid = request.path_params.get("skill_id", "")
    vid = request.path_params.get("version_id", "")
    v_mgr = getattr(runner.store, "version_mgr", None)
    if v_mgr is None:
        return err("version manager not ready", 503)
    result = v_mgr.rollback(sid, vid, runner.store)
    if result is None:
        return err("version not found or rollback failed", 404)
    return ok({"skill": result, "rolled_back_to": vid})


async def skill_version_diff(request: Request):
    """对比两个版本的差异。"""
    g = guard(request)
    if g:
        return g
    runner = _runner()
    if runner is None:
        return err("runner not ready", 503)
    sid = request.path_params.get("skill_id", "")
    va = request.query_params.get("a", "")
    vb = request.query_params.get("b", "")
    if not va or not vb:
        return err("need both a and b version ids")
    v_mgr = getattr(runner.store, "version_mgr", None)
    if v_mgr is None:
        return err("version manager not ready", 503)
    return ok(v_mgr.diff(sid, va, vb))


async def skill_sandbox_test(request: Request):
    """沙箱测试：dry_run 模式运行技能，生成测试报告。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    sandbox = getattr(rt, "sandbox_mgr", None)
    if sandbox is None:
        return err("sandbox manager not ready", 503)
    sid = request.path_params.get("skill_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    payload = body.get("payload") or {}
    report = await sandbox.test_skill(sid, payload)
    return ok(report)


async def skill_approve(request: Request):
    """审批通过技能。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    sandbox = getattr(rt, "sandbox_mgr", None)
    if sandbox is None:
        return err("sandbox manager not ready", 503)
    sid = request.path_params.get("skill_id", "")
    skill = sandbox.approve(sid)
    if skill is None:
        return err("skill not found", 404)
    return ok({"skill": skill})


async def skill_reject(request: Request):
    """拒绝审批技能。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    sandbox = getattr(rt, "sandbox_mgr", None)
    if sandbox is None:
        return err("sandbox manager not ready", 503)
    sid = request.path_params.get("skill_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = body.get("reason", "")
    skill = sandbox.reject(sid, reason)
    if skill is None:
        return err("skill not found", 404)
    return ok({"skill": skill})


async def sandbox_pending(request: Request):
    """列出所有待审批的技能。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    sandbox = getattr(rt, "sandbox_mgr", None)
    if sandbox is None:
        return err("sandbox manager not ready", 503)
    return ok({"items": sandbox.list_pending(), "count": len(sandbox.list_pending())})


async def conflict_detect(request: Request):
    """执行冲突检测，返回报告。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    detector = getattr(rt, "conflict_detector", None)
    if detector is None:
        return err("conflict detector not ready", 503)
    report = detector.detect_all()
    return ok(report)


async def conflict_report(request: Request):
    """获取最新冲突报告。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    detector = getattr(rt, "conflict_detector", None)
    if detector is None:
        return err("conflict detector not ready", 503)
    report = detector.get_latest_report()
    if report is None:
        return ok({"message": "no report yet, run detect first"})
    return ok(report)


async def template_list(request: Request):
    """列出技能模板。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    tpl_mgr = getattr(rt, "template_mgr", None)
    if tpl_mgr is None:
        return err("template manager not ready", 503)
    category = request.query_params.get("category")
    templates = tpl_mgr.list_templates(category)
    return ok({"items": templates, "count": len(templates)})


async def template_categories(request: Request):
    """列出模板分类。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    tpl_mgr = getattr(rt, "template_mgr", None)
    if tpl_mgr is None:
        return err("template manager not ready", 503)
    return ok({"items": tpl_mgr.list_categories()})


async def template_get(request: Request):
    """获取单个模板详情。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    tpl_mgr = getattr(rt, "template_mgr", None)
    if tpl_mgr is None:
        return err("template manager not ready", 503)
    tid = request.path_params.get("template_id", "")
    tpl = tpl_mgr.get_template(tid)
    if tpl is None:
        return err("template not found", 404)
    return ok(tpl)


async def template_create(request: Request):
    """基于模板创建技能。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    tpl_mgr = getattr(rt, "template_mgr", None)
    if tpl_mgr is None:
        return err("template manager not ready", 503)
    try:
        body = await request.json()
    except Exception:
        return err("invalid json body")
    tid = body.get("template_id", "")
    params = body.get("params", {})
    skill_id = body.get("skill_id")
    try:
        skill = tpl_mgr.create_from_template(tid, params, skill_id)
        return ok({"skill": skill, "message": "技能已创建为草稿，请确认后启用"})
    except ValueError as e:
        return err(str(e), 400)


async def perf_dashboard(request: Request):
    """性能仪表盘汇总。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    perf = getattr(rt, "perf_monitor", None)
    if perf is None:
        return err("performance monitor not ready", 503)
    return ok(perf.get_dashboard())


async def perf_skills(request: Request):
    """技能性能统计。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    perf = getattr(rt, "perf_monitor", None)
    if perf is None:
        return err("performance monitor not ready", 503)
    skill_id = request.query_params.get("skill_id")
    window = int(request.query_params.get("window", 3600))
    return ok(perf.get_skill_stats(skill_id, window))


async def perf_llm(request: Request):
    """LLM调用统计。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    perf = getattr(rt, "perf_monitor", None)
    if perf is None:
        return err("performance monitor not ready", 503)
    window = int(request.query_params.get("window", 3600))
    return ok(perf.get_llm_stats(window))


async def perf_triggers(request: Request):
    """触发器性能统计。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    perf = getattr(rt, "perf_monitor", None)
    if perf is None:
        return err("performance monitor not ready", 503)
    trigger_id = request.query_params.get("trigger_id")
    window = int(request.query_params.get("window", 3600))
    return ok(perf.get_trigger_stats(trigger_id, window))


async def perf_alerts(request: Request):
    """性能告警。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    perf = getattr(rt, "perf_monitor", None)
    if perf is None:
        return err("performance monitor not ready", 503)
    alerts = perf.get_alerts()
    return ok({"items": alerts, "count": len(alerts)})


async def collab_agents(request: Request):
    """列出所有协作Agent。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    collab = getattr(rt, "collab_mgr", None)
    if collab is None:
        return err("collaboration manager not ready", 503)
    return ok({"items": collab.list_agents(), "count": len(collab.list_agents())})


async def collab_register(request: Request):
    """注册Agent能力。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    collab = getattr(rt, "collab_mgr", None)
    if collab is None:
        return err("collaboration manager not ready", 503)
    try:
        body = await request.json()
    except Exception:
        return err("invalid json body")
    agent = collab.register_agent(
        agent_id=body.get("agent_id", ""),
        name=body.get("name", ""),
        role=body.get("role", ""),
        capabilities=body.get("capabilities", []),
        rooms=body.get("rooms"),
        members=body.get("members"),
        description=body.get("description", ""),
    )
    return ok(agent)


async def collab_route(request: Request):
    """智能路由：根据任务选择最合适的Agent。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    collab = getattr(rt, "collab_mgr", None)
    if collab is None:
        return err("collaboration manager not ready", 503)
    try:
        body = await request.json()
    except Exception:
        return err("invalid json body")
    target = collab.route_task(
        task=body.get("task", ""),
        room=body.get("room"),
        member=body.get("member"),
        required_capabilities=body.get("capabilities"),
    )
    if target is None:
        return err("no suitable agent found", 404)
    return ok(target)


async def collab_delegate(request: Request):
    """委派任务给另一个Agent。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    collab = getattr(rt, "collab_mgr", None)
    if collab is None:
        return err("collaboration manager not ready", 503)
    try:
        body = await request.json()
    except Exception:
        return err("invalid json body")
    result = await collab.delegate_task(
        delegator=body.get("delegator", "butler"),
        task=body.get("task", ""),
        target_agent=body.get("target_agent"),
        context=body.get("context"),
        room=body.get("room"),
        member=body.get("member"),
    )
    return ok(result)


async def collab_stats(request: Request):
    """协作统计。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    collab = getattr(rt, "collab_mgr", None)
    if collab is None:
        return err("collaboration manager not ready", 503)
    return ok(collab.get_collaboration_stats())


async def evolution_dashboard(request: Request):
    """自进化仪表盘。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    evo = getattr(rt, "self_evolution", None)
    if evo is None:
        return err("self evolution engine not ready", 503)
    return ok(evo.get_dashboard())


async def evolution_analyze(request: Request):
    """执行自进化分析，生成改进建议。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    evo = getattr(rt, "self_evolution", None)
    if evo is None:
        return err("self evolution engine not ready", 503)
    suggestions = evo.analyze()
    return ok({"suggestions": suggestions, "count": len(suggestions)})


async def evolution_execute(request: Request):
    """执行改进建议。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    evo = getattr(rt, "self_evolution", None)
    if evo is None:
        return err("self evolution engine not ready", 503)
    try:
        body = await request.json()
    except Exception:
        return err("invalid json body")
    import asyncio
    suggestion_id = body.get("suggestion_id", "")
    result = await evo.execute_suggestion(suggestion_id)
    return ok(result)


async def evolution_rewrite_suggestion(request: Request):
    """M9: 用 LLM 为指定技能生成重写建议。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    evo = getattr(rt, "self_evolution", None)
    if evo is None:
        return err("self evolution engine not ready", 503)
    try:
        body = await request.json()
    except Exception:
        return err("invalid json body")
    skill_id = body.get("skill_id", "")
    if not skill_id:
        return err("skill_id required")
    result = await evo.llm_rewrite_suggestion(skill_id)
    return ok(result)


async def evolution_suggestions(request: Request):
    """获取改进建议列表。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    evo = getattr(rt, "self_evolution", None)
    if evo is None:
        return err("self evolution engine not ready", 503)
    status = request.query_params.get("status")
    suggestions = evo.get_suggestions(status)
    return ok({"items": suggestions, "count": len(suggestions)})


async def evolution_history(request: Request):
    """获取自进化历史。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    evo = getattr(rt, "self_evolution", None)
    if evo is None:
        return err("self evolution engine not ready", 503)
    limit = int(request.query_params.get("limit", 50))
    return ok({"items": evo.get_history(limit), "count": len(evo.get_history(limit))})


async def evolution_mode(request: Request):
    """设置自进化模式。"""
    g = guard(request)
    if g:
        return g
    from butler.runtime import get_runtime
    rt = get_runtime()
    evo = getattr(rt, "self_evolution", None)
    if evo is None:
        return err("self evolution engine not ready", 503)
    try:
        body = await request.json()
    except Exception:
        return err("invalid json body")
    mode = body.get("mode", "semi")
    evo.set_mode(mode)
    return ok({"mode": mode})


async def skill_run(request: Request):
    """编排层主入口：POST /api/skill/{id}/run。熔断返回 429。"""
    g = guard(request)
    if g:
        return g
    runner = _runner()
    if runner is None:
        return err("runner not ready", 503)
    sid = request.path_params.get("skill_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    res = await runner.run(sid, source="api", payload=body)
    if res.get("status") == "breaker":
        return err(res.get("error", "breaker"), 429)
    return ok(res)


async def skill_test(request: Request):
    """WebUI 试跑：dry_run（不推送输出），disabled 也可跑。"""
    g = guard(request)
    if g:
        return g
    runner = _runner()
    if runner is None:
        return err("runner not ready", 503)
    sid = request.path_params.get("skill_id", "")
    res = await runner.run(sid, source="test", dry_run=True)
    return ok(res)


async def skill_runs(request: Request):
    g = guard(request)
    if g:
        return g
    sid = request.path_params.get("skill_id", "")
    limit = int(request.query_params.get("limit", "50"))
    rows = await asyncio.to_thread(repo_recent, sid, limit)
    return ok({"items": rows})


async def skill_stats(request: Request):
    """单个技能执行统计（M6 质量评估）。"""
    g = guard(request)
    if g:
        return g
    sid = request.path_params.get("skill_id", "")
    days = int(request.query_params.get("days", "30"))
    from butler.store import repo
    stats = await asyncio.to_thread(repo.get_skill_stats, sid, days)
    return ok(stats)


async def skills_stats_all(request: Request):
    """所有技能执行统计，按成功率升序（低成功率在前）。"""
    g = guard(request)
    if g:
        return g
    days = int(request.query_params.get("days", "30"))
    from butler.store import repo
    stats = await asyncio.to_thread(repo.get_all_skill_stats, days)
    return ok({"items": stats, "count": len(stats)})


async def skills_low_quality(request: Request):
    """低质量技能发现（执行次数>=min_runs且成功率<=max_rate）。"""
    g = guard(request)
    if g:
        return g
    days = int(request.query_params.get("days", "30"))
    min_runs = int(request.query_params.get("min_runs", "5"))
    max_rate = float(request.query_params.get("max_rate", "0.6"))
    from butler.store import repo
    items = await asyncio.to_thread(repo.get_low_quality_skills, days, min_runs, max_rate)
    return ok({"items": items, "count": len(items)})


def repo_recent(skill_id: str, limit: int):
    from butler.store import repo
    return repo.recent_skill_runs(skill_id, limit)


async def skill_natural_create(request: Request):
    """自然语言生成技能：描述 → LLM → 预览 → 草稿。"""
    g = guard(request)
    if g:
        return g
    body = await request.json()
    description = body.get("description", "").strip()
    if not description:
        return err("description 不能为空")

    rt = get_runtime()
    llm = getattr(rt, "llm", None)
    if not llm:
        return err("LLM 未初始化")

    creator = getattr(rt, "skill_creator", None)
    if not creator:
        return err("技能生成器未初始化")

    # 直接用 async llm.chat 生成技能 JSON
    from butler.skills.creator import TRIGGER_GUIDE, ENGINE_GUIDE
    prompt = f"""你是技能生成器。根据用户描述生成一个豆包管家技能的 JSON 定义。

{TRIGGER_GUIDE}
{ENGINE_GUIDE}

用户描述：{description}

请输出 JSON 格式的技能定义，包含以下字段：
- id: 小写字母数字下划线，2-40位
- name: 技能名称
- trigger: {{"entry": "触发入口"}}（定时触发用 "schedule"，并加 cron 字段如 "0 8 * * *"）
- senses: [{{"type": "camera", "room": "房间名"}}]
- brain: {{"type": "类型", "engine": "引擎名", "mode": "模式", "prompt": "", "system": "", "context": [], "dedup": false, "max_chars": 80, "mess_slot": false}}
  - 如果 engine 是 "cron_task"，brain 还需包含：api_id、condition、message_template
- output: [{{"type": "输出类型", "tts": true}}]
- limits: {{"per_day": 数字}}
- on_busy: "drop" 或 "queue"
- role: "butler"

已注册的 API 列表（用户描述中提到天气相关时用 caiyunweather）：
- caiyunweather: 彩云天气 API（支持 hour_local 条件按本地小时匹配降水概率）

只输出 JSON，不要其他文字。"""

    try:
        text, _ = await llm.chat(
            "你是技能生成专家，只输出 JSON。",
            [{"role": "user", "content": prompt}],
            max_tokens=1500, temperature=0.3
        )
    except Exception as e:
        return err(f"LLM 调用失败: {e}")

    # 提取 JSON
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.startswith("json"):
            text = text[4:]
    import json as _json
    try:
        skill_json = _json.loads(text)
    except Exception:
        return err("LLM 返回的 JSON 解析失败，请重试或换个描述")

    # 创建草稿
    result = creator.create_draft(skill_json, role_id="webui")
    if not result.get("ok"):
        return err(result.get("error", "创建草稿失败"))

    return ok({
        "draft_id": result["draft"].get("draft_id"),
        "preview": result["preview"],
        "skill": result["draft"],
    })


async def skill_mock_test(request: Request):
    """mock 测试：不调真实工具，用预设场景验证 LLM 决策。"""
    g = guard(request)
    if g:
        return g
    runner = _runner()
    if runner is None:
        return err("runner not ready", 503)
    sid = request.path_params.get("skill_id", "")
    skill = runner.store.get(sid)
    if skill is None:
        return err("skill not found", 404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    scenarios = body.get("scenarios") or [
        {"name": "电视关着", "mock": {"tv_on": False, "weather": "晴 33度", "time": "07:30"}},
        {"name": "电视开着", "mock": {"tv_on": True, "tv_package": "com.mitv.tvhome", "weather": "多云 26度", "time": "07:30"}},
    ]
    user_replies = body.get("user_replies") or ["好", "不用"]
    from butler.skills.engines.llm_decide.mock import mock_test
    results = await mock_test(skill, runner.rt, scenarios, user_replies)
    return ok({"skill_id": sid, "results": results})



def routes():

    return [
        Route("/api/skills", skills_list, methods=["GET"]),
        Route("/api/skills", skills_save, methods=["POST"]),
        Route("/api/skills/reload", skills_reload, methods=["POST"]),
        Route("/api/skills/{skill_id}", skills_delete, methods=["DELETE"]),
        Route("/api/skill/{skill_id}/status", skill_set_status, methods=["PUT"]),
        Route("/api/skill/{skill_id}/quarantine", skill_quarantine, methods=["POST"]),
        Route("/api/skills/drafts", skill_drafts_list, methods=["GET"]),
        Route("/api/skills/drafts/confirm", skill_draft_confirm, methods=["POST"]),
        Route("/api/skills/natural-create", skill_natural_create, methods=["POST"]),
        Route("/api/skills/drafts/cancel", skill_draft_cancel, methods=["POST"]),
        Route("/api/skills/quarantine/stats", quarantine_stats, methods=["GET"]),
        Route("/api/skills/quarantine/auto-recover", quarantine_auto_recover, methods=["POST"]),
        Route("/api/skill/{skill_id}/quarantine/restore", quarantine_restore, methods=["POST"]),
        Route("/api/skill/{skill_id}/quarantine/audit", quarantine_audit, methods=["GET"]),
        Route("/api/skill/{skill_id}/versions", skill_versions, methods=["GET"]),
        Route("/api/skill/{skill_id}/versions/{version_id}", skill_version_detail, methods=["GET"]),
        Route("/api/skill/{skill_id}/versions/{version_id}/rollback", skill_rollback, methods=["POST"]),
        Route("/api/skill/{skill_id}/diff", skill_version_diff, methods=["GET"]),
        Route("/api/skill/{skill_id}/sandbox-test", skill_sandbox_test, methods=["POST"]),
        Route("/api/skill/{skill_id}/approve", skill_approve, methods=["POST"]),
        Route("/api/skill/{skill_id}/reject", skill_reject, methods=["POST"]),
        Route("/api/skills/sandbox/pending", sandbox_pending, methods=["GET"]),
        Route("/api/conflict/detect", conflict_detect, methods=["POST"]),
        Route("/api/conflict/report", conflict_report, methods=["GET"]),
        Route("/api/templates", template_list, methods=["GET"]),
        Route("/api/templates/categories", template_categories, methods=["GET"]),
        Route("/api/templates/{template_id}", template_get, methods=["GET"]),
        Route("/api/templates/create", template_create, methods=["POST"]),
        Route("/api/perf/dashboard", perf_dashboard, methods=["GET"]),
        Route("/api/perf/skills", perf_skills, methods=["GET"]),
        Route("/api/perf/llm", perf_llm, methods=["GET"]),
        Route("/api/perf/triggers", perf_triggers, methods=["GET"]),
        Route("/api/perf/alerts", perf_alerts, methods=["GET"]),
        Route("/api/collab/agents", collab_agents, methods=["GET"]),
        Route("/api/collab/register", collab_register, methods=["POST"]),
        Route("/api/collab/route", collab_route, methods=["POST"]),
        Route("/api/collab/delegate", collab_delegate, methods=["POST"]),
        Route("/api/collab/stats", collab_stats, methods=["GET"]),
        Route("/api/evolution/dashboard", evolution_dashboard, methods=["GET"]),
        Route("/api/evolution/analyze", evolution_analyze, methods=["POST"]),
        Route("/api/evolution/execute", evolution_execute, methods=["POST"]),
        Route("/api/evolution/rewrite-suggestion", evolution_rewrite_suggestion, methods=["POST"]),
        Route("/api/evolution/suggestions", evolution_suggestions, methods=["GET"]),
        Route("/api/evolution/history", evolution_history, methods=["GET"]),
        Route("/api/evolution/mode", evolution_mode, methods=["POST"]),
        Route("/api/skill/{skill_id}/run", skill_run, methods=["POST"]),
        Route("/api/skill/{skill_id}/test", skill_test, methods=["POST"]),
        Route("/api/skill/{skill_id}/mock", skill_mock_test, methods=["POST"]),
                Route("/api/skill/{skill_id}/runs", skill_runs, methods=["GET"]),
        Route("/api/skill/{skill_id}/stats", skill_stats, methods=["GET"]),
        Route("/api/skills/stats", skills_stats_all, methods=["GET"]),
        Route("/api/skills/low-quality", skills_low_quality, methods=["GET"]),
    ]
