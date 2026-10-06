/* 记忆页逻辑（v1.1.1：审核 + 投喂） */
document.addEventListener("alpine:init", () => {
  const s = Alpine.store("app");

  // ---- 原有：本地流水 + 语义记忆 ----
  s.loadMemory = async function() {
    const [loc, sem] = await Promise.all([API.memoryLocal(100), API.memorySemantic()]);
    this.recent = loc && loc.data ? loc.data.turns : this.recent;
    this.semantic = sem && sem.data ? sem.data.memories : [];
    this.loadFacts();
    this.loadFeedOverview();
  };
  s.delTurn = async function(id) {
    await API.memoryDelete(id);
    this.loadRecent();
  };
  s.addMemory = async function() {
    const content = (this.$refs && this.$refs.memContent && this.$refs.memContent.value) || "";
    const refs = (this.$refs && this.$refs.memRefs && this.$refs.memRefs.value) || "";
    if (!content) return this.showToast("请输入记忆内容");
    const r = await API.memoryAdd(content, refs.split(",").map(s => s.trim()).filter(Boolean), "");
    this.showToast(r && r.ok ? "记忆已写入" : (r && r.error) || "写入失败");
    this.loadMemory();
  };

  // ---- v1.1 记忆事实审核 ----
  s.facts = [];
  s.factCounts = { pending: 0, approved: 0, rejected: 0 };
  s.factStatusFilter = "pending";
  s.factTypeFilter = "";
  s.factEditing = null;
  s.factEditText = "";
  s.factRunning = false;

  s.loadFacts = async function() {
    const r = await API.memoryFacts(this.factStatusFilter, this.factTypeFilter);
    if (r && r.data) {
      this.facts = r.data.facts || [];
      this.factCounts = r.data.counts || { pending: 0, approved: 0, rejected: 0 };
    }
  };
  s.setFactFilter = function(status) { this.factStatusFilter = status; this.loadFacts(); };
  s.setFactTypeFilter = function(t) { this.factTypeFilter = (this.factTypeFilter === t) ? "" : t; this.loadFacts(); };
  s.approveFact = async function(id) {
    const r = await API.memoryFactApprove(id);
    if (r && r.ok) { this.showToast("已通过"); this.loadFacts(); this.loadFeedOverview(); }
  };
  s.rejectFact = async function(id) {
    const r = await API.memoryFactReject(id);
    if (r && r.ok) { this.showToast("已拒绝"); this.loadFacts(); this.loadFeedOverview(); }
  };
  s.deleteFact = async function(id) {
    if (!confirm("删除这条记忆？")) return;
    const r = await API.memoryFactDelete(id);
    if (r && r.ok) { this.showToast("已删除"); this.loadFacts(); this.loadFeedOverview(); }
  };
  s.startEditFact = function(f) { this.factEditing = f; this.factEditText = f.content; };
  s.cancelEditFact = function() { this.factEditing = null; this.factEditText = ""; };
  s.saveEditFact = async function() {
    if (!this.factEditing || !this.factEditText.trim()) return;
    const r = await API.memoryFactUpdate(this.factEditing.id, { content: this.factEditText.trim() });
    if (r && r.ok) { this.showToast("已保存"); this.factEditing = null; this.loadFacts(); this.loadFeedOverview(); }
  };
  s.runExtract = async function() {
    if (this.factRunning) return;
    this.factRunning = true;
    this.showToast("正在分析对话…");
    try {
      const r = await API.memoryExtractRun(7);
      if (r && r.ok) {
        const d = r.data || {};
        this.showToast(`提取完成：新增 ${d.extracted || 0} 条，去重 ${d.skipped_dup || 0} 条`);
        this.loadFacts(); this.loadFeedOverview();
      } else {
        this.showToast((r && r.error) || "提取失败");
      }
    } finally { this.factRunning = false; }
  };
  s.factTypeLabel = function(t) { return { preference: "偏好", family: "家庭", event: "事件", habit: "习惯" }[t] || t; };
  s.factTypeColor = function(t) {
    return {
      preference: "bg-blue-500/20 text-blue-300",
      family: "bg-purple-500/20 text-purple-300",
      event: "bg-amber-500/20 text-amber-300",
      habit: "bg-green-500/20 text-green-300",
    }[t] || "bg-white/10 text-gray-300";
  };

  // ---- v1.1.1 投喂 ----
  s.feedOverview = {};           // {role_id: {name, conversation_id, bound, pending_count, fed_count, changed_count, failed_count, facts: {pending, fed, changed}}}
  s.feedBusy = "";               // 正在投喂的 role_id
  s.expandedRoles = {};          // 折叠状态
  s.bindingRole = null;          // 正在重绑定的 role_id
  s.bindingConvId = "";

  s.loadFeedOverview = async function() {
    const r = await API.memoryFeedOverview();
    if (r && r.data && r.data.overview) {
      this.feedOverview = r.data.overview;
      // 默认展开有待投喂的角色
      Object.keys(this.feedOverview).forEach(rid => {
        if (this.expandedRoles[rid] === undefined) {
          this.expandedRoles[rid] = this.feedOverview[rid].pending_count > 0;
        }
      });
    }
  };
  s.toggleRole = function(rid) {
    this.expandedRoles[rid] = !this.expandedRoles[rid];
  };
  s.feedRole = async function(rid) {
    if (this.feedBusy) return;
    this.feedBusy = rid;
    this.showToast(`正在投喂 ${this.feedOverview[rid].name}…`);
    try {
      const r = await API.memoryFeedRole(rid);
      if (r && r.ok) {
        const d = r.data || {};
        this.showToast(`投喂完成：${d.fed || 0} 条`);
      } else {
        this.showToast((r && r.message) || (r && r.error) || "投喂失败");
      }
    } finally { this.feedBusy = ""; this.loadFeedOverview(); }
  };
  s.feedOneFact = async function(fid) {
    const r = await API.memoryFeedFact(fid);
    if (r && r.ok) { this.showToast("已投喂"); }
    else { this.showToast((r && r.message) || (r && r.error) || "投喂失败"); }
    this.loadFacts(); this.loadFeedOverview();
  };
  s.startBind = function(rid) {
    this.bindingRole = rid;
    this.bindingConvId = (this.feedOverview[rid] && this.feedOverview[rid].conversation_id) || "";
  };
  s.cancelBind = function() { this.bindingRole = null; this.bindingConvId = ""; };
  s.saveBind = async function() {
    if (!this.bindingConvId.trim()) return this.showToast("请输入 conversation_id");
    const r = await API.memoryFeedBind(this.bindingRole, this.bindingConvId.trim());
    if (r && r.ok) {
      this.showToast("对话已绑定");
      this.bindingRole = null;
      this.loadFeedOverview();
    } else {
      this.showToast((r && r.error) || "绑定失败");
    }
  };
  s.changeFactRole = async function(f, newRole) {
    const r = await API.memoryFactSetRole(f.id, newRole);
    if (r && r.ok) { this.showToast("已改归属"); this.loadFacts(); this.loadFeedOverview(); }
  };
  s.feedStatusBadge = function(f) {
    if (f.feed_status === "fed") return { text: "已投喂", cls: "bg-green-500/20 text-green-300" };
    if (f.feed_status === "changed") return { text: "已变更·需重投", cls: "bg-amber-500/20 text-amber-300" };
    if (f.feed_status === "failed") return { text: "投喂失败", cls: "bg-red-500/20 text-red-300" };
    return { text: "待投喂", cls: "bg-white/10 text-gray-400" };
  };

  // ---- v1.1.2 对账 ----
  s.reconBusy = "";           // 正在对账的角色
  s.reconResult = {};         // {role_id: {confirmed, missing, hallucinated, conflicting, doubao_recall}}
  s.correctWrong = "";
  s.correctRight = "";

  s.runReconcile = async function(rid) {
    if (this.reconBusy) return;
    this.reconBusy = rid;
    this.showToast(`正在问豆包${this.feedOverview[rid].name}它记得什么…`);
    try {
      const r = await API.memoryReconcile(rid);
      if (r && r.ok && r.data) {
        this.reconResult[rid] = r.data;
        this.showToast("对账完成");
      } else {
        this.showToast((r && r.message) || "对账失败");
      }
    } finally {
      this.reconBusy = "";
    }
  };
  s.doCorrect = async function(rid) {
    if (!this.correctWrong.trim() || !this.correctRight.trim()) return this.showToast("两边都要填");
    const r = await API.memoryCorrect(rid, this.correctWrong.trim(), this.correctRight.trim());
    if (r && r.ok) {
      this.showToast("已发送纠正消息");
      this.correctWrong = "";
      this.correctRight = "";
    } else {
      this.showToast((r && r.message) || "纠正失败");
    }
  };

  // 一键投喂设备说明书
  s.deviceManualBusy = false;
  s.deviceManualReply = "";
  s.feedDeviceManual = async function() {
    if (this.deviceManualBusy) return;
    this.deviceManualBusy = true;
    this.showToast("正在投喂设备说明书到豆包管家…");
    try {
      const r = await API.memoryFeedDeviceManual();
      if (r && r.ok && r.data) {
        this.deviceManualReply = r.data.reply || "";
        this.showToast("投喂完成");
      } else {
        this.showToast((r && r.message) || "投喂失败");
      }
    } finally {
      this.deviceManualBusy = false;
    }
  };
});
