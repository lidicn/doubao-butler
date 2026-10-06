/* Koin Action【智动】页面 — v1.9
 * 功能：
 * 1. API 注册管理
 * 2. 任务列表
 * 3. 执行日志
 */
document.addEventListener("alpine:init", () => {
  Alpine.store("koin", {
    apis: [],
    tasks: [],
    logs: [],
    health: [],
    failures: [],
    loading: false,
    
    // 新建 API 表单
    newApi: {
      api_id: "",
      url: "",
      timeout: 10,
      method: "GET",
      headers_text: "",
      body_text: "",
    },
    
    // 新建任务表单
    newTask: {
      id: "",
      name: "",
      cron: "",
      api_id: "",
      jsonpath: "",
      comparator: "gt",
      value: "",
      message_template: "",
    },
    
    preview: null,
    previewValid: false,
    
    async init() {
      const app = Alpine.store('app');
      if (!app.authed) return;
      await this.loadApis();
      await this.loadLogs();
      await this.loadHealth();
      await this.loadFailures();
    },
    
    async loadApis() {
      const r = await fetch("/api/cron_task/apis");
      const d = await r.json();
      if (d.ok) this.apis = d.data;
    },
    
    async loadLogs() {
      const r = await fetch("/api/cron_task/logs?limit=20");
      const d = await r.json();
      if (d.ok) this.logs = d.data;
    },

    async loadHealth() {
      const r = await fetch("/api/cron_task/health");
      const d = await r.json();
      if (d.ok) this.health = d.data;
    },

    async loadFailures() {
      const r = await fetch("/api/cron_task/failures?limit=20");
      const d = await r.json();
      if (d.ok) this.failures = d.data;
    },
    
    async registerApi() {
      if (!this.newApi.api_id || !this.newApi.url) {
        alert("API ID 和 URL 不能为空");
        return;
      }
      this.loading = true;
      try {
        // 解析 headers（JSON 格式）
        let headers = {};
        if (this.newApi.headers_text.trim()) {
          try {
            headers = JSON.parse(this.newApi.headers_text);
          } catch {
            alert("Headers 格式错误，请输入合法 JSON，如 {\"Authorization\":\"Bearer xxx\"}");
            return;
          }
        }
        const body = {
          api_id: this.newApi.api_id,
          url: this.newApi.url,
          timeout: this.newApi.timeout,
          headers: headers,
          method: this.newApi.method,
          body: this.newApi.body_text.trim(),
        };
        const r = await fetch("/api/cron_task/apis", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const d = await r.json();
        if (d.ok) {
          this.newApi = { api_id: "", url: "", timeout: 10, method: "GET", headers_text: "", body_text: "" };
          await this.loadApis();
          Alpine.store("app").toast("API 注册成功");
        } else {
          alert(d.error || "注册失败");
        }
      } finally {
        this.loading = false;
      }
    },
    
    async unregisterApi(api_id) {
      if (!confirm(`确定注销 API ${api_id}？`)) return;
      await fetch(`/api/cron_task/apis/${api_id}`, { method: "DELETE" });
      await this.loadApis();
      Alpine.store("app").toast("API 已注销");
    },
    
    async previewTask() {
      const task = {
        id: this.newTask.id,
        name: this.newTask.name,
        trigger: {
          entry: "schedule",
          cron: this.newTask.cron,
        },
        brain: {
          type: "cron_task",
          api_id: this.newTask.api_id,
          condition: {
            jsonpath: this.newTask.jsonpath,
            comparator: this.newTask.comparator,
            value: this.newTask.value,
          },
          message_template: this.newTask.message_template,
        },
        output: [{ type: "xiaomi_speak", room: "客厅" }],
      };
      
      const r = await fetch("/api/cron_task/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task }),
      });
      const d = await r.json();
      if (d.ok) {
        this.preview = d.data.preview;
        this.previewValid = d.data.valid;
        if (!d.data.valid) {
          alert("校验失败：\n" + d.data.errors.join("\n"));
        }
      }
    },
    
    async saveTask() {
      if (!this.previewValid) {
        alert("请先预览并确保校验通过");
        return;
      }
      
      const task = {
        id: this.newTask.id,
        name: this.newTask.name,
        status: "draft",
        trigger: {
          entry: "schedule",
          cron: this.newTask.cron,
        },
        brain: {
          type: "cron_task",
          api_id: this.newTask.api_id,
          condition: {
            jsonpath: this.newTask.jsonpath,
            comparator: this.newTask.comparator,
            value: this.newTask.value,
          },
          message_template: this.newTask.message_template,
        },
        output: [{ type: "xiaomi_speak", room: "客厅" }],
      };
      
      this.loading = true;
      try {
        const r = await fetch("/api/skills", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(task),
        });
        const d = await r.json();
        if (d.ok) {
          this.newTask = {
            id: "", name: "", cron: "", api_id: "",
            jsonpath: "", comparator: "gt", value: "", message_template: "",
          };
          this.preview = null;
          Alpine.store("app").toast("任务已创建（草稿状态，需手动启用）");
        } else {
          alert(d.error || "创建失败");
        }
      } finally {
        this.loading = false;
      }
    },
    
    async testTask(skill_id) {
      this.loading = true;
      try {
        const r = await fetch(`/api/cron_task/test/${skill_id}`, { method: "POST" });
        const d = await r.json();
        if (d.ok) {
          alert(
            `执行结果：\n` +
            `状态：${d.data.status}\n` +
            `原因：${d.data.reason}\n` +
            `HTTP：${d.data.http_status}\n` +
            `条件满足：${d.data.condition_result}`
          );
          await this.loadLogs();
        }
      } finally {
        this.loading = false;
      }
    },
  });
});
