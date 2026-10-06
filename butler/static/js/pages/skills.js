/* 技能页逻辑 */
document.addEventListener("alpine:init", () => {
  const s = Alpine.store("app");
  s.skills = []; s.editingSkill = null;
  s.skillOut = { tv: false, tts: true, xiaomi: false, bark: false };
  s.skillRuns = []; s.runsFor = ""; s.testing = false; s.testResult = null;
  s.breaker = {}; s.engines = [];
  s.skillVersions = []; s.versionsFor = "";
  s.naturalDesc = ""; s.creating = false; s.draftPreview = null; s.draftId = null;

  s.naturalCreate = async function() {
    if (!this.naturalDesc.trim()) return;
    this.creating = true;
    this.draftPreview = null;
    try {
      const r = await fetch("/api/skills/natural-create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: this.naturalDesc }),
      });
      const d = await r.json();
      if (d.ok) {
        this.draftPreview = d.data.preview;
        this.draftId = d.data.draft_id;
      } else {
        alert(d.error || "生成失败");
      }
    } catch (e) {
      alert("请求失败: " + e.message);
    } finally {
      this.creating = false;
    }
  };

  s.confirmDraft = async function() {
    if (!this.draftId) return;
    const r = await fetch("/api/skills/drafts/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const d = await r.json();
    if (d.ok) {
      this.draftPreview = null;
      this.draftId = null;
      this.naturalDesc = "";
      this.toast = "技能已创建并启用";
      await this.loadSkills();
    } else {
      alert(d.error || "确认失败");
    }
  };

  s.cancelDraft = function() {
    this.draftPreview = null;
    this.draftId = null;
  };

  s.loadSkills = async function() {
    const r = await API.skills();
    if (r && r.data) {
      this.skills = r.data.items || [];
      this.breaker = r.data.breaker || {};
      this.engines = r.data.engines || [];
    }
  };
  s.openSkill = function(s2) {
    const base = s2 ? JSON.parse(JSON.stringify(s2)) : {
      id: "", name: "", version: 1, enabled: false, role: "butler",
      trigger: { entry: "webhook" },
      senses: [{ type: "camera", room: "客厅", max_age_s: 0 }],
      brain: { type: "vlm_compose", engine: "camera_vlm", mode: "", prompt: "", context: ["persona", "member_profile"], dedup: false, max_chars: 60 },
      output: [{ type: "tv_notify", tts: true }],
      limits: { per_day: 20 },
      on_busy: "drop",
    };
    if (!base.senses || !base.senses.length) base.senses = [{ type: "camera", room: "客厅", max_age_s: 0 }];
    if (!base.brain) base.brain = { type: "vlm_compose", engine: "camera_vlm", mode: "", prompt: "", context: [], dedup: false, max_chars: 60 };
    if (!base.limits) base.limits = { per_day: 20 };
    if (!base.trigger) base.trigger = { entry: "webhook" };
    base.contextText = (base.brain.context || []).join("、");
    const out = base.output || [];
    this.skillOut = {
      tv: out.some(o => o.type === "tv_notify"),
      tts: out.some(o => o.type === "tv_notify" && o.tts !== false),
      xiaomi: out.some(o => o.type === "xiaomi_speak"),
      bark: out.some(o => o.type === "bark"),
    };
    this.editingSkill = base;
    this.testResult = null;
  };
  s.newSkill = function() { this.openSkill(null); };
  s.newSkillFromEngine = function(e) {
    this.openSkill(null);
    if (this.editingSkill && e) this.editingSkill.brain.engine = e.id;
  };
  s.saveSkill = async function() {
    const sk = this.editingSkill;
    if (!sk.id.trim()) return this.showToast("技能 id 必填");
    if (!sk.name.trim()) return this.showToast("技能名称必填");
    if (typeof sk.contextText === "string") sk.brain.context = sk.contextText.split(/[，,、]/).map(x => x.trim()).filter(Boolean);
    delete sk.contextText;
    const out = [];
    if (this.skillOut.tv) out.push({ type: "tv_notify", tts: !!this.skillOut.tts });
    if (this.skillOut.xiaomi) out.push({ type: "xiaomi_speak" });
    if (this.skillOut.bark) out.push({ type: "bark" });
    sk.output = out;
    if (sk.brain.mode === "") delete sk.brain.mode;
    this.loading = true;
    const res = await API.skillSave(sk);
    this.loading = false;
    if (res && res.ok) { this.showToast("技能已保存"); this.editingSkill = null; this.loadSkills(); }
    else this.showToast((res && res.error) || "保存失败");
  };
  s.deleteSkill = async function(id) {
    const res = await API.skillDelete(id);
    this.showToast(res && res.ok ? "已删除" : "删除失败");
    this.loadSkills();
  };
  s.testSkill = async function(id) {
    this.testing = true; this.testResult = null;
    const res = await API.skillTest(id);
    this.testing = false;
    this.testResult = res && res.data ? res.data : { error: (res && res.error) || "试跑失败" };
    const t = (this.testResult.text || this.testResult.error || "试跑完成");
    this.showToast(t.length > 60 ? t.slice(0, 60) + "…" : t);
    this.loadRuns(id);
    this.loadSkills();
  };
  s.runSkill = async function(id) {
    const res = await API.skillRun(id, {});
    this.showToast(res && res.ok ? "已触发运行" : ((res && res.error) || "运行失败"));
    this.loadRuns(id);
    this.loadSkills();
  };
  s.loadRuns = async function(id) {
    this.runsFor = id;
    const r = await API.skillRuns(id, 50);
    this.skillRuns = r && r.data ? r.data.items : [];
  };
  s.toggleSkill = async function(id, enabled) {
    const r = await API.skillSetStatus(id, enabled ? "enabled" : "disabled");
    this.showToast(r && r.ok ? (enabled ? "已启用" : "已禁用") : "操作失败");
    this.loadSkills();
  };
  s.loadSkillVersions = async function(id) {
    this.versionsFor = id;
    const r = await API.skillVersions(id);
    this.skillVersions = r && r.data ? r.data.versions : [];
  };
  s.rollbackSkill = async function(id, version) {
    if (!confirm("确定回滚到此版本？当前版本会被保存为历史版本。")) return;
    const r = await API.skillRollback(id, version);
    this.showToast(r && r.ok ? "已回滚" : "回滚失败");
    this.loadSkills();
    this.loadSkillVersions(id);
  };
  s.breakerOf = function(id) { return this.breaker && this.breaker[id] ? this.breaker[id] : null; };
});
