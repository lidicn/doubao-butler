#!/usr/bin/env python3
"""PWA 添加指令中心 Tab：nav + mobileNav + section + store 逻辑"""
import re

base = '/vol1/1000/docker/doubao-butler/butler/static'

# ========== 1. 修改 app.js ==========
with open(f'{base}/js/app.js', 'r', encoding='utf-8') as f:
    js = f.read()

# 1a. nav 数组添加 commands（在 skills 之后）
old_nav_skills = '      { k: "skills", l: "技能", i: "🧩" },\n      { k: "triggers", l: "触发器", i: "⚡" },'
new_nav_skills = '      { k: "skills", l: "技能", i: "🧩" },\n      { k: "commands", l: "指令", i: "📋" },\n      { k: "triggers", l: "触发器", i: "⚡" },'
if old_nav_skills in js:
    js = js.replace(old_nav_skills, new_nav_skills)
    print('app.js: nav 添加 commands ✅')
else:
    print('app.js: nav 未找到匹配')

# 1b. mobileNav 把 triggers 换成 commands
old_mobile = '      { k: "triggers", l: "触发", i: "⚡" },'
new_mobile = '      { k: "commands", l: "指令", i: "📋" },'
if old_mobile in js:
    js = js.replace(old_mobile, new_mobile)
    print('app.js: mobileNav triggers→commands ✅')
else:
    print('app.js: mobileNav 未找到匹配')

# 1c. 添加 commands store 状态（在 nav 定义之后，APP_VERSION 之前）
old_ver = '    APP_VERSION: "v1.2.0",'
commands_state = '''    // 指令中心状态（v1.2）
    commands: [], commandsLoading: false, commandsFilter: "all", commandsTarget: "all",
    commandDetail: null, commandDetailLoading: false,
    newCmdTarget: "TP", newCmdText: "", newCmdPriority: "normal", newCmdSaving: false,
    pendingCount: 0,

    APP_VERSION: "v1.2.0",'''
if old_ver in js:
    js = js.replace(old_ver, commands_state)
    print('app.js: 添加 commands store 状态 ✅')
else:
    print('app.js: APP_VERSION 未找到')

# 1d. 添加 commands 方法（在 switchView 方法附近）
# 找一个合适的位置插入方法
old_switch = '    switchView(v) { this.view = v; if (v === "overview") this.loadOverview(); }'
new_switch = '''    switchView(v) {
      this.view = v;
      if (v === "overview") this.loadOverview();
      if (v === "commands") { this.loadCommands(); this.loadPendingCount(); }
    },

    // === 指令中心方法 ===
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
    },'''
if old_switch in js:
    js = js.replace(old_switch, new_switch)
    print('app.js: 添加 commands 方法 ✅')
else:
    print('app.js: switchView 未找到匹配')

with open(f'{base}/js/app.js', 'w', encoding='utf-8') as f:
    f.write(js)

# ========== 2. 修改 index.html ==========
with open(f'{base}/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 在 dialog section 之后插入 commands section
# 先找 dialog section 的结束位置
dialog_start = html.find("x-show=\"$store.app.view==='dialog'\"")
if dialog_start < 0:
    print('index.html: 未找到 dialog section')
else:
    # 找 dialog section 之后的下一个 section 开始
    next_section = html.find("<section x-show=", dialog_start + 50)
    if next_section < 0:
        print('index.html: 未找到下一个 section')
    else:
        commands_section = '''      <!-- 指令中心 -->
      <section x-show="$store.app.view==='commands'" class="space-y-4 fade-in">
        <div class="flex items-center justify-between">
          <h2 class="text-lg font-bold flex items-center gap-2">📋 指令中心
            <span class="text-xs px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-400" x-text="pendingCount + ' 待处理'"></span>
          </h2>
          <button @click="loadCommands(); loadPendingCount()" class="text-xs px-3 py-1 rounded-lg bg-white/10 hover:bg-white/20">刷新</button>
        </div>

        <!-- 创建新指令 -->
        <div class="glass p-4 space-y-3">
          <div class="text-sm font-medium">下发新指令</div>
          <div class="flex gap-2 flex-wrap">
            <select x-model="newCmdTarget" class="flex-1 min-w-[80px] bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm">
              <option value="TP">TP (TVPilot)</option>
              <option value="DP">DP (DeskPilot)</option>
              <option value="PM">PM (管家)</option>
            </select>
            <select x-model="newCmdPriority" class="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm">
              <option value="normal">普通</option>
              <option value="urgent">紧急</option>
            </select>
          </div>
          <textarea x-model="newCmdText" rows="2" placeholder="指令内容，如：修复 mytv HTTP API 换台接口"
            class="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm resize-none"></textarea>
          <button @click="createCommand" :disabled="newCmdSaving || !newCmdText.trim()"
            class="w-full py-2 rounded-lg bg-blue-500 hover:bg-blue-600 disabled:opacity-50 text-sm font-medium">
            <span x-text="newCmdSaving ? '发送中...' : '下发指令'"></span>
          </button>
        </div>

        <!-- 过滤 -->
        <div class="flex gap-2 flex-wrap text-xs">
          <button @click="commandsFilter='all'; loadCommands()" :class="commandsFilter==='all' ? 'bg-blue-500' : 'bg-white/10'" class="px-3 py-1 rounded-full">全部</button>
          <button @click="commandsFilter='pending'; loadCommands()" :class="commandsFilter==='pending' ? 'bg-amber-500' : 'bg-white/10'" class="px-3 py-1 rounded-full">待处理</button>
          <button @click="commandsFilter='accepted'; loadCommands()" :class="commandsFilter==='accepted' ? 'bg-blue-500' : 'bg-white/10'" class="px-3 py-1 rounded-full">进行中</button>
          <button @click="commandsFilter='completed'; loadCommands()" :class="commandsFilter==='completed' ? 'bg-green-500' : 'bg-white/10'" class="px-3 py-1 rounded-full">已完成</button>
          <select @change="loadCommands()" x-model="commandsTarget" class="bg-white/10 rounded-full px-3 py-1 ml-auto">
            <option value="all">全部目标</option>
            <option value="TP">TP</option>
            <option value="DP">DP</option>
            <option value="PM">PM</option>
          </select>
        </div>

        <!-- 指令列表 -->
        <div class="space-y-2">
          <div x-show="commandsLoading" class="text-center text-sm text-gray-400 py-8">加载中...</div>
          <div x-show="!commandsLoading && commands.length===0" class="text-center text-sm text-gray-400 py-8">暂无指令</div>
          <template x-for="cmd in commands" :key="cmd.id">
            <div class="glass p-3 cursor-pointer hover:bg-white/5" @click="loadCommandDetail(cmd.id)">
              <div class="flex items-start justify-between gap-2">
                <div class="flex-1 min-w-0">
                  <div class="text-sm font-medium truncate" x-text="cmd.text"></div>
                  <div class="flex items-center gap-2 mt-1 text-xs text-gray-400">
                    <span x-text="cmd.target"></span>
                    <span>·</span>
                    <span x-text="cmd.created_at ? cmd.created_at.substring(0,16) : ''"></span>
                    <span x-show="cmd.priority==='urgent'" class="text-red-400">紧急</span>
                  </div>
                </div>
                <span class="text-xs px-2 py-0.5 rounded-full bg-white/10 whitespace-nowrap" :class="cmdStatusColor(cmd.status)" x-text="cmdStatusText(cmd.status)"></span>
              </div>
            </div>
          </template>
        </div>

        <!-- 指令详情弹窗 -->
        <div x-show="commandDetail" class="fixed inset-0 bg-black/60 z-50 flex items-end md:items-center justify-center" @click.self="commandDetail=null">
          <div class="glass w-full md:max-w-lg max-h-[80vh] overflow-y-auto rounded-t-2xl md:rounded-2xl p-4 space-y-3">
            <div class="flex items-center justify-between">
              <h3 class="font-bold">指令详情</h3>
              <button @click="commandDetail=null" class="text-gray-400 hover:text-white text-xl">×</button>
            </div>
            <template x-if="commandDetail">
              <div class="space-y-3">
                <div class="text-sm" x-text="commandDetail.text"></div>
                <div class="flex gap-4 text-xs text-gray-400">
                  <span>目标: <span x-text="commandDetail.target"></span></span>
                  <span>优先级: <span x-text="commandDetail.priority"></span></span>
                  <span>创建: <span x-text="commandDetail.created_by"></span></span>
                </div>
                <div class="flex items-center gap-2">
                  <span class="text-xs px-2 py-0.5 rounded-full bg-white/10" :class="cmdStatusColor(commandDetail.status)" x-text="cmdStatusText(commandDetail.status)"></span>
                  <button x-show="commandDetail.status==='pending' || commandDetail.status==='accepted'" @click="cancelCommand(commandDetail.id)" class="text-xs px-3 py-1 rounded-lg bg-red-500/20 text-red-400 hover:bg-red-500/30">取消指令</button>
                </div>
                <div x-show="commandDetail.result" class="text-xs bg-white/5 rounded-lg p-3">
                  <div class="text-gray-400 mb-1">完成说明</div>
                  <div x-text="commandDetail.result"></div>
                </div>
                <div x-show="commandDetail.error" class="text-xs bg-red-500/10 rounded-lg p-3 text-red-400">
                  <div class="mb-1">错误</div>
                  <div x-text="commandDetail.error"></div>
                </div>
                <!-- 事件历史 -->
                <div class="space-y-1">
                  <div class="text-xs text-gray-400 font-medium">状态流转</div>
                  <template x-for="ev in (commandDetail.events || [])" :key="ev.id">
                    <div class="flex items-center gap-2 text-xs">
                      <span class="w-2 h-2 rounded-full bg-blue-400"></span>
                      <span class="text-gray-300" x-text="ev.event_type"></span>
                      <span class="text-gray-500" x-text="ev.created_at ? ev.created_at.substring(0,16) : ''"></span>
                      <span x-show="ev.reporter" class="text-gray-500" x-text="'by ' + ev.reporter"></span>
                    </div>
                  </template>
                </div>
              </div>
            </template>
          </div>
        </div>
      </section>

'''
        html = html[:next_section] + commands_section + html[next_section:]
        print('index.html: 添加 commands section ✅')

with open(f'{base}/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

# ========== 验证 ==========
print()
print('=== 验证 ===')
with open(f'{base}/js/app.js', 'r', encoding='utf-8') as f:
    j = f.read()
print('nav commands:', '✅' if 'k: "commands"' in j else '❌')
print('mobileNav commands:', '✅' if 'mobileNav' in j and 'commands' in j.split('mobileNav')[1].split(']')[0] else '❌')
print('commands store:', '✅' if 'commandsLoading' in j else '❌')
print('loadCommands 方法:', '✅' if 'async loadCommands' in j else '❌')
print('createCommand 方法:', '✅' if 'async createCommand' in j else '❌')

with open(f'{base}/index.html', 'r', encoding='utf-8') as f:
    h = f.read()
print('commands section:', '✅' if "view==='commands'" in h else '❌')
print('创建指令表单:', '✅' if 'newCmdText' in h else '❌')
print('指令详情弹窗:', '✅' if 'commandDetail' in h else '❌')
print('完成！')
