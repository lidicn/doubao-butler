/* 豆包管家 WebUI — 主框架（Alpine store 基础状态 + 通用方法）
 * 页面逻辑拆分到 js/pages/*.js，各模块在 alpine:init 时往 store 注入方法。
 */
document.addEventListener("alpine:init", () => {
  Alpine.store("app", {
    // ===== 基础状态 =====
    view: "overview",
    authed: false,
    authRequired: false,
    user: "", pass: "", loginErr: "",
    status: null, config: null, recent: [], semantic: [], members: [], fp: null,
    tts: null, streamLog: [], loading: false, toast: "", toastTimer: null,
    logs: [],  // v2.7: 技能运行日志',

    // ===== 导航 =====
    nav: [
      { k: "overview", l: "概览", i: "🏠" },
      { k: "butler_chat", l: "管家", i: "🤖" },
      { k: "push", l: "推送", i: "📣" },
      { k: "dialog", l: "对话", i: "💬" },
      { k: "persona", l: "人格", i: "🎭" },
      { k: "roles", l: "角色", i: "👥" },
      { k: "skills", l: "技能", i: "🧩" },
      { k: "commands", l: "指令", i: "📋" },
      { k: "triggers", l: "触发器", i: "⚡" },
      { k: "profile", l: "画像", i: "🪪" },
      { k: "memory", l: "记忆", i: "🧠" },
      { k: "evolution", l: "自进化", i: "🔄" },
      { k: "devices", l: "设备", i: "🔌" },
      { k: "logs", l: "日志", i: "📜" },
      { k: "settings", l: "设置", i: "⚙️" },
    ],
    mobileNav: [
      { k: "overview", l: "概览", i: "🏠" },
      { k: "dialog", l: "对话", i: "💬" },
      { k: "skills", l: "技能", i: "🧩" },
      { k: "commands", l: "指令", i: "📋" },
      { k: "settings", l: "设置", i: "⚙️" },
    ],

    APP_VERSION: "v1.4.0",

    // ===== 通用方法 =====
    async init() {
      API._onUnauth = () => { this.authed = false; };
      try {
        const st = await API.loginStatus();
        this.authRequired = !!(st && st.data && st.data.auth_required);
        this.authed = !!(st && st.data && st.data.authed);
      } catch (e) { this.authRequired = false; }
      if (this.authed) this.enter();
    },
    async login() {
      const r = await API.login(this.user, this.pass);
      if (r && r.ok) { this.authed = true; this.loginErr = ""; this.enter(); }
      else { this.loginErr = (r && r.error) || "登录失败"; }
    },
    async logout() {
      try { await API.logout(); } catch(e) {}
      this.authed = false;
      this.user = ""; this.pass = "";
      location.reload();
    },
    enter() {
      this.loadAll();
      this.connectStream();
    },
    async loadLogs() {
      try {
        const r = await fetch("/api/skill/runs?limit=50", {
          headers: {"Authorization": "Bearer " + (localStorage.getItem("token") || "")}
        });
        const data = await r.json();
        this.logs = (data && data.data && data.data.items) || data.items || [];
      } catch (e) { console.error("loadLogs failed", e); }
    },
    async loadAll() {
      this.loading = true;
      try {
        const [s, c, rec, mem, fp, t, d, logs] = await Promise.all([
          API.status(), API.config(), API.dialogRecent(30), API.memoryMembers(), API.memoryFp(), API.ttsEngines(), API.devices(),
          fetch("/api/skill/runs?limit=50", {headers: {"Authorization": "Bearer " + localStorage.getItem("token")}}).then(r=>r.json()).catch(()=>({items:[]})),
        ]);
        this.logs = logs && logs.data ? logs.data.items : (logs.items || []);
        this.status = s && s.data ? s.data : null;
        this.config = c && c.data ? c.data : null;
        this.recent = rec && rec.data ? rec.data.turns : [];
        this.members = mem && mem.data ? mem.data.members : [];
        this.fp = fp && fp.data ? fp.data : null;
        this.tts = t && t.data ? t.data : null;
        this.devices = d && d.data ? d.data.devices : [];
      } catch (e) {}
      this.loading = false;
    },
    connectStream() {
      try {
        this.es = API.stream();
        this.es.addEventListener("dialog", (ev) => {
          try {
            const p = JSON.parse(ev.data);
            this.streamLog.unshift(p);
            this.streamLog = this.streamLog.slice(0, 40);
            if (p.type === "speak") {
              this.recent.unshift({ ts: p.ts, member: p.member, role: "butler", text: p.text });
              this.recent = this.recent.slice(0, 30);
              if (this.status) {
                this.status.state = this.status.state || {};
                this.status.state.last_speak_member = p.member;
              }
            }
          } catch (e) {}
        });
      } catch (e) {}
    },
    switchView(v) {
      this.view = v;
      if (v === "memory") this.loadMemory();
      if (v === "config") this.loadConfig();
      if (v === "dialog") this.loadRecent();
      if (v === "push") this.loadNotifyHistory();
      if (v === "roles") { this.loadRoles(); this.loadDevices(); }
      if (v === "skills") this.loadSkills();
      if (v === "triggers") this.loadTriggers();
      if (v === "commands") { this.loadCommands(); this.loadPendingCount(); }
      if (v === "profile") this.loadProfiles();
      if (v === "devices") { this.loadHaDevices(); this.loadHaDomains(); }
      if (v === "evolution") this.loadEvolution();
      if (v === "overview") this.loadScenes();
      if (v === "butler_chat") this.chatHistory = this.chatHistory || [];
    },
    // v1.5 管家对话
    chatHistory: [],
    chatInput: "",
    chatLoading: false,
    async sendChat() {
      const text = this.chatInput.trim();
      if (!text || this.chatLoading) return;
      this.chatHistory.push({ who: "我", text });
      this.chatInput = "";
      this.chatLoading = true;
      try {
        const r = await fetch("/api/agent/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, member: "大佬" })
        });
        const data = await r.json();
        if (data.ok && data.data?.reply) {
          this.chatHistory.push({ who: "管家", text: data.data.reply });
        } else {
          this.chatHistory.push({ who: "系统", text: "错误：" + (data.error || "未知") });
        }
      } catch (e) {
        this.chatHistory.push({ who: "系统", text: "网络错误：" + e.message });
      }
      this.chatLoading = false;
    },
    // v1.4 快捷场景
    scenes: [], sceneRunning: "", sceneResult: null,
    async loadScenes() {
      try {
        const r = await fetch("/api/scenes", { headers: { } });
        const d = await r.json();
        this.scenes = (d && d.data && d.data.scenes) ? d.data.scenes : [];
      } catch(e) { this.scenes = []; }
    },
    async runScene(id) {
      if (this.sceneRunning) return;
      this.sceneRunning = id;
      this.sceneResult = null;
      try {
        const r = await fetch(`/api/scenes/${id}/run`, { method: "POST", headers: { } });
        const d = await r.json();
        this.sceneResult = (d && d.data) ? d.data : null;
      } catch(e) { this.showToast("场景执行失败"); }
      this.sceneRunning = "";
    },
    // 共享方法（被多个页面调用）
    async loadRecent() { const r = await API.dialogRecent(60); this.recent = r && r.data ? r.data.turns : []; },
    async loadConfig() { const r = await API.config(); this.config = r && r.data ? r.data : this.config; },
    showToast(msg) {
      this.toast = msg;
      clearTimeout(this.toastTimer);
      this.toastTimer = setTimeout(() => (this.toast = ""), 2600);
    },
    fmtTime(ts) {
      if (!ts) return "";
      const d = new Date(ts * 1000);
      return d.toLocaleString("zh-CN", { hour12: false });
    },
    fmtTs(ts) { try { return new Date(ts * 1000).toLocaleString("zh-CN"); } catch (e) { return ts; } },
  });
});


// v2.5: TTS 队列状态
async function loadTtsQueue() {
  try {
    const r = await fetch('/api/tts/queue/status');
    const j = await r.json();
    if (j.ok) window.ttsQueue = j.data;
  } catch (e) { console.warn('tts queue status failed', e); }
}

async function testTtsQueue() {
  try {
    await fetch('/api/tts/queue/enqueue', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: '队列测试', priority: 3})
    });
    setTimeout(loadTtsQueue, 1000);
  } catch (e) { console.warn('tts enqueue failed', e); }
}

// Auto-refresh TTS queue every 5s when authed
setInterval(() => {
  if (window.$store && $store.app && $store.app.authed) loadTtsQueue();
}, 5000);
