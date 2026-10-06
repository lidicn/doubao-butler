/* 技能调试面板（v2.7 · 格16，2026-09-30）
 * 消费四个现成端点，本单⛔ 不加任何后端接口：
 *   GET  /api/agent/traces?limit&member&status        列表（butler/api/agent_routes.py:289）
 *   GET  /api/agent/traces/{trace_id}                 整行摊开＝变量查看
 *   GET  /api/triggers/logs?limit&trace_id            v2.6#3 触发日志（trigger_routes.py:308）
 *   POST /api/triggers/{trigger_id}/test              触发模拟（trigger_routes.py:312）
 * 单步执行不提供：agent_traces 只在一次 finish 时落一条 INSERT（butler/agent_trace.py:84），
 * 过程中没有可回放的落点。触发模拟走面板上的「试运行／真执行」开关（DCD 裁定 20261001 §二 B1）：
 * 缺省＝试运行（body 带 dry_run: true，引擎闸门 butler/triggers/engine.py:337 只评估不执行）。
 */
document.addEventListener("alpine:init", () => {
  const s = Alpine.store("app");

  s.dbgTraces = []; s.dbgTraceLimit = 50; s.dbgMember = ""; s.dbgStatus = "";
  s.dbgTrace = null; s.dbgTraceLoading = false; s.dbgSteps = [];
  s.dbgTriggers = []; s.dbgTestBusy = ""; s.dbgTestResult = null; s.dbgDryRun = true;
  s.dbgLogs = []; s.dbgLogTrace = ""; s.dbgLogLoading = false;

  s.loadDebug = async function () {
    await Promise.all([this.loadDbgTraces(), this.loadDbgTriggers(), this.loadDbgLogs()]);
  };

  s.loadDbgTraces = async function () {
    try {
      let url = "/api/agent/traces?limit=" + (this.dbgTraceLimit || 50);
      if (this.dbgMember) url += "&member=" + encodeURIComponent(this.dbgMember);
      if (this.dbgStatus) url += "&status=" + encodeURIComponent(this.dbgStatus);
      const r = await fetch(url, { credentials: "same-origin" });
      const d = await r.json();
      this.dbgTraces = (d && d.data && d.data.traces) ? d.data.traces : [];
    } catch (e) { console.error("loadDbgTraces", e); this.dbgTraces = []; }
  };

  s.openDbgTrace = async function (traceId) {
    if (!traceId) return;
    this.dbgTraceLoading = true; this.dbgTrace = null; this.dbgSteps = [];
    try {
      const r = await fetch("/api/agent/traces/" + encodeURIComponent(traceId), { credentials: "same-origin" });
      const d = await r.json();
      const row = (d && d.data) ? d.data : null;
      this.dbgTrace = row;
      if (row && Array.isArray(row.steps)) this.dbgSteps = row.steps;
      this.dbgLogTrace = traceId;
      await this.loadDbgLogs();
    } catch (e) {
      console.error("openDbgTrace", e);
      this.showToast("trace 读取失败");
    }
    this.dbgTraceLoading = false;
  };

  // 变量查看：整行 key/value 摊开。steps 由后端解析成数组后单独渲染，这里不重复摊。
  s.dbgVars = function () {
    const t = this.dbgTrace;
    if (!t) return [];
    return Object.keys(t).filter((k) => k !== "steps").map((k) => {
      const v = t[k];
      return { k: k, v: (v !== null && typeof v === "object") ? JSON.stringify(v) : String(v) };
    });
  };

  s.closeDbgTrace = function () { this.dbgTrace = null; this.dbgSteps = []; };

  s.loadDbgTriggers = async function () {
    try {
      const r = await fetch("/api/triggers", { credentials: "same-origin" });
      const d = await r.json();
      this.dbgTriggers = (d && d.data && d.data.items) ? d.data.items : [];
    } catch (e) { console.error("loadDbgTriggers", e); this.dbgTriggers = []; }
  };

  s.loadDbgLogs = async function () {
    this.dbgLogLoading = true;
    try {
      let url = "/api/triggers/logs?limit=50";
      if (this.dbgLogTrace) url += "&trace_id=" + encodeURIComponent(this.dbgLogTrace);
      const r = await fetch(url, { credentials: "same-origin" });
      const d = await r.json();
      this.dbgLogs = (d && d.data && d.data.items) ? d.data.items : [];
    } catch (e) { console.error("loadDbgLogs", e); this.dbgLogs = []; }
    this.dbgLogLoading = false;
  };

  s.clearDbgLogTrace = function () { this.dbgLogTrace = ""; this.loadDbgLogs(); };

  s.dbgSkillNames = function (l) {
    const arr = (l && (l.skills || l.actions)) || [];
    return arr.map((x) => (x && x.skill) ? x.skill : x).join(" → ");
  };

  s.runDbgTrigger = async function (id) {
    if (!id) return;
    const dry = this.dbgDryRun !== false;   // 取不到开关＝试运行（fail-closed，⛔ 抽个 undefined 就真播报）
    if (!window.confirm(dry ? "「模拟触发」＝试运行：并发/资源/冷却三道闸照常判定，动作不跑——不播报、不发通知、不写执行流水。确定试跑？" : "「模拟触发」＝真执行：播报和通知会真的发出去，并写 trigger_runs 执行流水。确定继续？")) return;
    this.dbgTestBusy = id; this.dbgTestResult = null;
    try {
      const r = await fetch("/api/triggers/" + encodeURIComponent(id) + "/test", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ payload: {}, dry_run: dry }),
      });
      const d = await r.json();
      this.dbgTestResult = (d && d.data) ? d.data : { error: (d && d.error) || "请求失败" };
      await this.loadDbgLogs();
      if (d && d.ok) this.showToast((dry ? "试运行完成（动作未执行）：" : "已模拟触发：") + id);
    } catch (e) {
      this.dbgTestResult = { error: String(e) };
    }
    this.dbgTestBusy = "";
  };
});
