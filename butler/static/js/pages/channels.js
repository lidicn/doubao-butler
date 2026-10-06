/* 通道配置页：Bark / 微信 / 小米 */
document.addEventListener("alpine:init", () => {
  const s = Alpine.store("app");

  // ===== Bark =====
  s.barkStatus = null;
  s.barkTestTitle = "测试";
  s.barkTestBody = "这是一条 Bark 测试推送";
  s.barkTestLevel = "active";
  s.barkTestSound = "";

  s.loadBarkStatus = async function() {
    const r = await fetch("/api/bark/status", {  });
    const j = await r.json();
    this.barkStatus = j.data;
  };

  s.testBark = async function() {
    this.loading = true;
    const r = await fetch("/api/bark/push", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: this.barkTestTitle,
        body: this.barkTestBody,
        level: this.barkTestLevel,
        sound: this.barkTestSound || undefined,
      }),
    });
    const j = await r.json();
    this.loading = false;
    this.showToast(j.data && j.data.pushed ? "Bark 推送成功" : "Bark 推送失败");
  };

  // ===== 小米/miio =====
  s.xiaomiStatus = null;
  s.xiaoaiDevices = [];

  s.loadXiaomiStatus = async function() {
    // 从 devices 接口拿所有小米设备
    const r = await fetch("/api/devices", {  });
    const j = await r.json();
    const devs = (j.data && j.data.devices) || [];
    this.xiaoaiDevices = devs.filter(d => d.type === "xiaomi");
  };

  // ===== 微信 =====
  s.wechatStatus = { configured: false, channel: "wechat/user_id" };

  s.loadWechatStatus = async function() {
    // 微信状态从 config 里拿
    const r = await fetch("/api/config", {  });
    const j = await r.json();
    const cfg = j.data || {};
    this.wechatStatus = {
      configured: !!(cfg.wechat_channel || cfg.iLlink_token),
      channel: cfg.wechat_channel || "wechat/user_id",
    };
  };

  // ===== 初始化 =====
  s.initChannels = function() {
    this.loadBarkStatus();
    this.loadXiaomiStatus();
    this.loadWechatStatus();
  };

  // ===== 快速路由 =====
  s.fastRoutes = [];
  s.frCandidates = [];
  s.frAnalyzing = false;

  s.loadFastRoutes = async function() {
    const r = await fetch("/api/agent/fast-routes", {  });
    const j = await r.json();
    this.fastRoutes = (j.data && j.data.routes) || [];
  };

  s.analyzeFastRoutes = async function() {
    this.frAnalyzing = true;
    const r = await fetch("/api/agent/fast-routes/analyze?min_count=2", {  });
    const j = await r.json();
    this.frCandidates = (j.data && j.data.candidates) || [];
    this.frAnalyzing = false;
    this.showToast(`找到 ${this.frCandidates.length} 条候选规则`);
  };

  s.addFastRoute = async function(c) {
    this.loading = true;
    const r = await fetch("/api/agent/fast-routes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: c.suggested_tool + " (自动)",
        keywords: c.keywords,
        tool: c.suggested_tool,
        args: JSON.parse(c.suggested_args || "{}"),
      }),
    });
    const j = await r.json();
    this.loading = false;
    this.showToast(j.ok ? "已添加" : "添加失败");
    this.loadFastRoutes();
    this.frCandidates = this.frCandidates.filter(x => x.pattern !== c.pattern);
  };

  s.toggleFastRoute = async function(r) {
    await fetch(`/api/agent/fast-routes/${r.id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !r.enabled }),
    });
    this.loadFastRoutes();
  };

  s.deleteFastRoute = async function(r) {
    await fetch(`/api/agent/fast-routes/${r.id}`, {
      method: "DELETE",
    });
    this.loadFastRoutes();
  };

  // ===== 设备别名 =====
  s.aliases = [];

  s.loadAliases = async function() {
    const r = await fetch("/api/agent/aliases", {  });
    const j = await r.json();
    this.aliases = (j.data && j.data.aliases) || [];
  };

  s.deleteAlias = async function(a) {
    await fetch(`/api/agent/aliases/${encodeURIComponent(a.alias)}`, {
      method: "DELETE",
    });
    this.loadAliases();
  };

  // ===== Token 使用量 =====
  s.tokenStats = null;

  s.loadTokenStats = async function() {
    const r = await fetch("/api/perf/llm?window=86400", {  });
    const j = await r.json();
    this.tokenStats = j.data;
  };

  // ===== 失败模式学习 =====
  s.failResult = null;
  s.failAnalyzing = false;

  s.analyzeFailures = async function() {
    this.failAnalyzing = true;
    const r = await fetch("/api/agent/failures/analyze?days=7", {  });
    const j = await r.json();
    this.failResult = j.data;
    this.failAnalyzing = false;
    this.showToast(`分析完成，找到 ${(j.data.suggestions || []).length} 条优化建议`);
  };

  // ===== 场景自动推断 =====
  s.sceneResult = null;
  s.sceneInferring = false;

  s.inferScenes = async function() {
    this.sceneInferring = true;
    const r = await fetch("/api/agent/scenes/infer?days=7", {  });
    const j = await r.json();
    this.sceneResult = j.data;
    this.sceneInferring = false;
    this.showToast(`分析完成，找到 ${(j.data.scenes || []).length} 个场景候选`);
    this.loadReports();
  };

  // ===== 自进化历史报告 =====
  s.failureReports = [];
  s.sceneReports = [];

  s.loadReports = async function() {
        const r1 = await fetch("/api/agent/failures/reports", {  });
    const j1 = await r1.json();
    this.failureReports = (j1.data && j1.data.reports) || [];

    const r2 = await fetch("/api/agent/scenes/reports", {  });
    const j2 = await r2.json();
    this.sceneReports = (j2.data && j2.data.reports) || [];
  };

  // ===== 主动感知（v1.7） =====
  s.perceptionEvents = [];
  s.perceptionState = {};
  s.presenceUsers = {};

  s.loadPerception = async function() {
        const r1 = await fetch("/api/events/recent?seconds=600", {  });
    const j1 = await r1.json();
    this.perceptionEvents = (j1.data && j1.data.events) || [];

    const r2 = await fetch("/api/state/current", {  });
    const j2 = await r2.json();
    this.perceptionState = (j2.data && j2.data.state) || {};

    const r3 = await fetch("/api/presence/snapshot", {  });
    const j3 = await r3.json();
    this.presenceUsers = (j3.data && j3.data.users) || {};
  };
});
