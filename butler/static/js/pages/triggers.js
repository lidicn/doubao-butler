/* 触发器页逻辑 */
document.addEventListener("alpine:init", () => {
  const s = Alpine.store("app");
  s.triggers = []; s.editingTrigger = null; s.triggerTemplates = null;
  s.triggerRuns = []; s.triggerRunsFor = "";

  s.loadTriggers = async function() {
    const r = await API.triggers();
    if (r && r.data) { this.triggers = r.data.items || []; }
    if (!this.triggerTemplates) {
      const t = await API.triggerTemplates();
      this.triggerTemplates = t && t.data ? t.data : null;
    }
  };
  s.openTrigger = function(t) {
    this.editingTrigger = t ? JSON.parse(JSON.stringify(t)) : {
      id: "", name: "", enabled: true, event: "face_detected",
      conditions: { time_range: "", member: "", room: "", text_contains: [] },
      role: "butler", actions: [{ skill: "", params: {} }],
      cooldown_sec: 300, priority: 10,
    };
    if (!this.editingTrigger.conditions) this.editingTrigger.conditions = {};
    if (!this.editingTrigger.actions || !this.editingTrigger.actions.length) {
      this.editingTrigger.actions = [{ skill: "", params: {} }];
    }
  };
  s.newTrigger = function() { this.openTrigger(null); };
  s.saveTrigger = async function() {
    const t = this.editingTrigger;
    if (!t.id.trim()) return this.showToast("触发器 id 必填");
    if (!t.name.trim()) return this.showToast("名称必填");
    this.loading = true;
    const res = await API.triggerSave(t);
    this.loading = false;
    if (res && res.ok) { this.showToast("已保存"); this.editingTrigger = null; this.loadTriggers(); }
    else this.showToast((res && res.error) || "保存失败");
  };
  s.deleteTrigger = async function(id) {
    if (!confirm("确定删除此触发器？")) return;
    const res = await API.triggerDelete(id);
    this.showToast(res && res.ok ? "已删除" : "删除失败");
    this.loadTriggers();
  };
  s.toggleTrigger = async function(id, enabled) {
    const r = await API.triggerSetEnabled(id, enabled);
    this.showToast(r && r.ok ? (enabled ? "已启用" : "已禁用") : "操作失败");
    this.loadTriggers();
  };
  s.loadTriggerRuns = async function(id) {
    this.triggerRunsFor = id;
    const r = await API.triggerRuns(id, 50);
    this.triggerRuns = r && r.data ? r.data.items : [];
  };

  // v1.8 触发注册中心健康监控
  s.triggersHealthData = null;
  s.loadTriggersHealth = async function() {
    const r = await API.triggersHealth();
    if (r && r.data) { this.triggersHealthData = r.data; }
  };
});
