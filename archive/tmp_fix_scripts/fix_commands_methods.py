#!/usr/bin/env python3
"""修复：添加 commands 方法到 app.js"""

path = '/vol1/1000/docker/doubao-butler/butler/static/js/app.js'
with open(path, 'r', encoding='utf-8') as f:
    js = f.read()

# 1. switchView 添加 commands 分支
old_trigger = '      if (v === "triggers") this.loadTriggers();'
new_trigger = '''      if (v === "triggers") this.loadTriggers();
      if (v === "commands") { this.loadCommands(); this.loadPendingCount(); }'''
if old_trigger in js and 'loadCommands' not in js:
    js = js.replace(old_trigger, new_trigger)
    print('switchView 添加 commands 分支 ✅')
else:
    print('switchView: 已添加或未找到匹配')

# 2. 在文件末尾（最后一个 } 之前）添加 commands 方法
# 找一个合适的插入点：在 cmdStatusColor 之前（如果不存在），或者在文件末尾
if 'async loadCommands' not in js:
    methods = '''
    // === 指令中心方法（v1.2）===
    async loadCommands() {
      this.commandsLoading = true;
      try {
        let url = "/api/commands?limit=50";
        if (this.commandsTarget !== "all") url += "&target=" + this.commandsTarget;
        if (this.commandsFilter !== "all") url += "&status=" + this.commandsFilter;
        const r = await fetch(url, { headers: { "Authorization": "Bearer admin" } });
        const d = await r.json();
        this.commands = (d && d.data && d.data.items) ? d.data.items : (d.data || []);
      } catch(e) { console.error("loadCommands", e); this.commands = []; }
      this.commandsLoading = false;
    },
    async loadPendingCount() {
      try {
        const r = await fetch("/api/commands/pending-count", { headers: { "Authorization": "Bearer admin" } });
        const d = await r.json();
        this.pendingCount = (d && d.data) ? (d.data.total || d.data.count || 0) : 0;
      } catch(e) { this.pendingCount = 0; }
    },
    async createCommand() {
      if (!this.newCmdText.trim()) { alert("指令内容不能为空"); return; }
      this.newCmdSaving = true;
      try {
        const r = await fetch("/api/commands", {
          method: "POST",
          headers: { "Content-Type": "application/json", "Authorization": "Bearer admin" },
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
    },
    async loadCommandDetail(id) {
      this.commandDetailLoading = true;
      try {
        const r = await fetch("/api/commands/" + id, { headers: { "Authorization": "Bearer admin" } });
        const d = await r.json();
        this.commandDetail = (d && d.data) ? d.data : null;
      } catch(e) { this.commandDetail = null; }
      this.commandDetailLoading = false;
    },
    async cancelCommand(id) {
      if (!confirm("确认取消这条指令？")) return;
      try {
        await fetch("/api/commands/" + id + "/cancel", {
          method: "POST", headers: { "Content-Type": "application/json", "Authorization": "Bearer admin" },
          body: JSON.stringify({ reason: "PWA 用户取消" })
        });
        this.commandDetail = null;
        this.loadCommands();
        this.loadPendingCount();
      } catch(e) { alert("取消失败: " + e.message); }
    },
    cmdStatusColor(s) {
      return { pending: "text-amber-400", accepted: "text-blue-400", completed: "text-green-400", failed: "text-red-400", cancelled: "text-gray-400" }[s] || "text-gray-400";
    },
    cmdStatusText(s) {
      return { pending: "待处理", accepted: "进行中", completed: "已完成", failed: "失败", cancelled: "已取消" }[s] || s;
    },
'''
    # 插入到文件倒数第二个 } 之前（Alpine.store 的结束）
    # 找最后一个 "  });" 或 "})"
    last_store_end = js.rfind('  });')
    if last_store_end < 0:
        last_store_end = js.rfind('})')
    if last_store_end > 0:
        js = js[:last_store_end] + methods + js[last_store_end:]
        print('添加 commands 方法 ✅')
    else:
        print('未找到插入点')
else:
    print('commands 方法已存在')

with open(path, 'w', encoding='utf-8') as f:
    f.write(js)

# 验证
with open(path, 'r', encoding='utf-8') as f:
    j = f.read()
print()
print('=== 验证 ===')
print('loadCommands:', '✅' if 'async loadCommands' in j else '❌')
print('createCommand:', '✅' if 'async createCommand' in j else '❌')
print('loadCommandDetail:', '✅' if 'async loadCommandDetail' in j else '❌')
print('cancelCommand:', '✅' if 'async cancelCommand' in j else '❌')
print('cmdStatusColor:', '✅' if 'cmdStatusColor' in j else '❌')
print('switchView commands 分支:', '✅' if 'v === "commands"' in j else '❌')
print('完成！')
