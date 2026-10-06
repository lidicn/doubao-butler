/* 后端 API 封装 */
const API = {
  async _req(method, path, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    const r = await fetch(path, opt);
    if (r.status === 401) {
      this._onUnauth && this._onUnauth();
      throw new Error("unauth");
    }
    try {
      return await r.json();
    } catch (e) {
      return { ok: false, error: "bad_response" };
    }
  },
  login(user, password) { return this._req("POST", "/api/login", { user, password }); },
  loginStatus() { return this._req("GET", "/api/login/status"); },
  logout() { return this._req("POST", "/api/logout"); },
  health() { return this._req("GET", "/api/health"); },
  status() { return this._req("GET", "/api/status"); },
  dialogTest(member, text) { return this._req("POST", "/api/dialog/test", { member, text }); },
  dialogRecent(limit = 50) { return this._req("GET", `/api/dialog/recent?limit=${limit}`); },
  dialogState() { return this._req("GET", "/api/dialog/state"); },
  config() { return this._req("GET", "/api/config"); },
  savePersona(system, greeting) { return this._req("POST", "/api/config/persona", { system, greeting_template: greeting }); },
  saveMember(m) { return this._req("POST", "/api/config/member", m); },
  saveWakeup(o) { return this._req("POST", "/api/config/wakeup", o); },
  memoryLocal(limit = 100) { return this._req("GET", `/api/memory/local?limit=${limit}`); },
  memoryDelete(id) { return this._req("DELETE", `/api/memory/local/${id}`); },
  memorySemantic() { return this._req("GET", "/api/memory/semantic"); },
  memoryAdd(content, refs, member) { return this._req("POST", "/api/memory/semantic", { content, source_refs: refs, member }); },
  memoryMembers() { return this._req("GET", "/api/memory/members"); },
  memoryFp() { return this._req("GET", "/api/memory/fingerprints"); },
  // v1.1 记忆事实审核
  memoryFacts(status, factType) {
    const q = new URLSearchParams();
    if (status) q.set("status", status);
    if (factType) q.set("fact_type", factType);
    const qs = q.toString();
    return this._req("GET", `/api/memory/facts${qs ? "?" + qs : ""}`);
  },
  memoryFactApprove(id) { return this._req("POST", `/api/memory/facts/${id}/approve`); },
  memoryFactReject(id) { return this._req("POST", `/api/memory/facts/${id}/reject`); },
  memoryFactDelete(id) { return this._req("DELETE", `/api/memory/facts/${id}`); },
  memoryFactUpdate(id, body) { return this._req("PUT", `/api/memory/facts/${id}`, body); },
  memoryExtractRun(days) { return this._req("POST", "/api/memory/facts/extract/run", { days: days || 7 }); },
  // v1.1.1 投喂
  memoryFeedOverview() { return this._req("GET", "/api/memory/feed/overview"); },
  memoryFeedRole(roleId) { return this._req("POST", `/api/memory/feed/${roleId}`); },
  memoryFeedFact(id) { return this._req("POST", `/api/memory/facts/${id}/feed`); },
  memoryFeedBind(roleId, convId) { return this._req("POST", "/api/memory/feed/bind", { role_id: roleId, conversation_id: convId }); },
  memoryFactSetRole(id, role) { return this._req("PUT", `/api/memory/facts/${id}/role`, { target_role: role }); },
  // v1.1.2 对账
  memoryReconcile(roleId) { return this._req("POST", `/api/memory/reconcile/${roleId}`); },
  memoryCorrect(roleId, wrong, right) { return this._req("POST", `/api/memory/correct/${roleId}`, { wrong, right }); },
  memoryFeedDeviceManual() { return this._req("POST", "/api/memory/feed-device-manual"); },
  ttsTry(text, member, voice) { return this._req("POST", "/api/tts/try", { text, member, voice }); },
  ttsSpeak(data) { return this._req("POST", "/api/tts/speak", data); },
  ttsEngines() { return this._req("GET", "/api/tts/engines"); },
  ttsVoices(voices) { return this._req("POST", "/api/tts/voices", { voices: voices || [] }); },
  roles() { return this._req("GET", "/api/roles"); },
  roleUpsert(body) { return this._req("POST", "/api/roles", body); },
  roleDelete(id) { return this._req("DELETE", `/api/roles/${encodeURIComponent(id)}`); },
  roleTry(id, text) { return this._req("POST", `/api/roles/${encodeURIComponent(id)}/try`, { text }); },
  devices() { return this._req("GET", "/api/devices"); },
  deviceUpsert(body) { return this._req("POST", "/api/devices", body); },
  deviceDelete(id) { return this._req("DELETE", `/api/devices/${encodeURIComponent(id)}`); },
  skills() { return this._req("GET", "/api/skills"); },
  skillSave(body) { return this._req("POST", "/api/skills", body); },
  skillDelete(id) { return this._req("DELETE", `/api/skills/${encodeURIComponent(id)}`); },
  skillRun(id, payload) { return this._req("POST", `/api/skill/${encodeURIComponent(id)}/run`, payload || {}); },
  skillTest(id) { return this._req("POST", `/api/skill/${encodeURIComponent(id)}/test`); },
  skillRuns(id, limit) { return this._req("GET", `/api/skill/${encodeURIComponent(id)}/runs?limit=${limit || 50}`); },
  skillSetStatus(id, status) { return this._req("PUT", `/api/skill/${encodeURIComponent(id)}/status`, { status }); },
  skillVersions(id) { return this._req("GET", `/api/skill/${encodeURIComponent(id)}/versions`); },
  skillRollback(id, version) { return this._req("POST", `/api/skill/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}/rollback`); },
  // 触发器
  triggers() { return this._req("GET", "/api/triggers"); },
  triggerSave(body) { return this._req("POST", "/api/triggers", body); },
  triggerDelete(id) { return this._req("DELETE", `/api/triggers/${encodeURIComponent(id)}`); },
  triggerSetEnabled(id, enabled) { return this._req("PUT", `/api/triggers/${encodeURIComponent(id)}/enabled`, { enabled }); },
  triggerTemplates() { return this._req("GET", "/api/triggers/templates"); },
  triggerRuns(id, limit) { return this._req("GET", `/api/triggers/${encodeURIComponent(id)}/runs?limit=${limit || 50}`); },
  profileMembers() { return this._req("GET", "/api/profile/members"); },
  profileGet(name) { return this._req("GET", `/api/profile/${encodeURIComponent(name)}`); },
  profilePut(name, profile) { return this._req("PATCH", `/api/profile/${encodeURIComponent(name)}`, { profile }); },
  profileOrganize(name, text) { return this._req("POST", "/api/profile/organize", { name, text }); },
  profileRevoke(memoryId) { return this._req("POST", "/api/profile/revoke", { memory_id: memoryId }); },
  notify(title, content, opts = {}) {
    return this._req("POST", "/api/notify", {
      title, content,
      type: opts.type || "info",
      important: !!opts.important,
      duration: opts.duration || 0,
      tts: opts.tts !== false,
      volume: opts.volume ?? 80,
      pause_media: !!opts.pause_media,
      voice: opts.voice || "",
      member: opts.member || "",
    });
  },
  notifyHistory(limit = 20) { return this._req("GET", `/api/notify/history?limit=${limit}`); },
  notifyDelete(id) { return this._req("DELETE", `/api/notify/${id}`); },
  uploadAvatar(name, file) {
    const fd = new FormData();
    fd.append("file", file);
    return fetch(`/api/avatars/${encodeURIComponent(name)}`, { method: "POST", body: fd })
      .then(r => { if (r.status === 401 && this._onUnauth) this._onUnauth(); return r.json(); });
  },
  avatarUrl(name, ts) { return `/avatars/${encodeURIComponent(name)}.png?v=${ts || Date.now()}`; },
  stream() { return new EventSource("/api/stream"); },

  // 自进化（v2.0）
  evolutionDashboard() { return this._req("GET", "/api/evolution/dashboard"); },
  evolutionAnalyze() { return this._req("POST", "/api/evolution/analyze"); },
  evolutionExecute(suggestionId) { return this._req("POST", "/api/evolution/execute", { suggestion_id: suggestionId }); },
  evolutionSuggestions(status) {
    const q = status ? `?status=${status}` : "";
    return this._req("GET", `/api/evolution/suggestions${q}`);
  },
  evolutionHistory(limit = 50) { return this._req("GET", `/api/evolution/history?limit=${limit}`); },
  evolutionSetMode(mode) { return this._req("POST", "/api/evolution/mode", { mode }); },
  agentTraces(limit = 50) { return this._req("GET", `/api/agent/traces?limit=${limit}`); },

  // HA 设备管理（v1.4）
  haDomains() { return this._req("GET", "/api/ha/domains"); },
  haDevices(params) {
    const q = new URLSearchParams(params || {}).toString();
    return this._req("GET", `/api/ha/devices${q ? "?" + q : ""}`);
  },
  haEntityMatch(name, domain) {
    const q = new URLSearchParams({ name, ...(domain ? { domain } : {}) }).toString();
    return this._req("GET", `/api/ha/entity-match?${q}`);
  },

  // 多Agent协作（v1.9）
  collabAgents() { return this._req("GET", "/api/collab/agents"); },
  collabStats() { return this._req("GET", "/api/collab/stats"); },
  collabRoute(task, room, member) {
    return this._req("POST", "/api/collab/route", { task, room, member });
  },
};
