/* 指令中心逻辑（v1.2） */
document.addEventListener("alpine:init", () => {
  const s = Alpine.store("app");
  s.commands = []; s.commandsLoading = false; s.commandsFilter = "all"; s.commandsTarget = "all";
  s.commandDetail = null; s.commandDetailLoading = false;
  s.newCmdTarget = "TP"; s.newCmdText = ""; s.newCmdPriority = "normal"; s.newCmdSaving = false;
  s.pendingCount = 0;

  s.loadCommands = async function() {
    this.commandsLoading = true;
    try {
      let url = "/api/commands?limit=50";
      if (this.commandsTarget !== "all") url += "&target=" + this.commandsTarget;
      if (this.commandsFilter !== "all") url += "&status=" + this.commandsFilter;
      const r = await fetch(url, { headers: { } });
      const d = await r.json();
      this.commands = (d && d.data && d.data.items) ? d.data.items : (d.data || []);
    } catch(e) { console.error("loadCommands", e); this.commands = []; }
    this.commandsLoading = false;
  };
  s.loadPendingCount = async function() {
    try {
      const r = await fetch("/api/commands/pending-count", { headers: { } });
      const d = await r.json();
      this.pendingCount = (d && d.data) ? (d.data.total || d.data.count || 0) : 0;
    } catch(e) { this.pendingCount = 0; }
  };
  s.createCommand = async function() {
    if (!this.newCmdText.trim()) { alert("指令内容不能为空"); return; }
    this.newCmdSaving = true;
    try {
      const r = await fetch("/api/commands", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: this.newCmdTarget, text: this.newCmdText, priority: this.newCmdPriority, created_by: "PWA" })
      });
      const d = await r.json();
      if (d && d.ok) {
        this.newCmdText = "";
        this.loadCommands();
        this.loadPendingCount();
      } else {
        alert("创建失败: " + (d.error || "未知错误"));
      }
    } catch(e) { alert("创建失败: " + e.message); }
    this.newCmdSaving = false;
  };
  s.loadCommandDetail = async function(id) {
    this.commandDetailLoading = true;
    try {
      const r = await fetch("/api/commands/" + id, { headers: { } });
      const d = await r.json();
      this.commandDetail = (d && d.data) ? d.data : null;
    } catch(e) { this.commandDetail = null; }
    this.commandDetailLoading = false;
  };
  s.cancelCommand = async function(id) {
    if (!confirm("确认取消这条指令？")) return;
    try {
      await fetch("/api/commands/" + id + "/cancel", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "PWA 用户取消" })
      });
      this.commandDetail = null;
      this.loadCommands();
      this.loadPendingCount();
    } catch(e) { alert("取消失败: " + e.message); }
  };
  s.cmdStatusColor = function(st) {
    return { pending: "text-amber-400", accepted: "text-blue-400", completed: "text-green-400", failed: "text-red-400", cancelled: "text-gray-400" }[st] || "text-gray-400";
  };
  s.cmdStatusText = function(st) {
    return { pending: "待处理", accepted: "进行中", completed: "已完成", failed: "失败", cancelled: "已取消" }[st] || st;
  };
});
