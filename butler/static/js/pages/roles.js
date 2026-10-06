/* 角色页逻辑 */
document.addEventListener("alpine:init", () => {
  const s = Alpine.store("app");
  s.roles = []; s.editingRole = null;
  s.voicesList = [
    { v: "zh-CN-XiaoxiaoNeural", l: "晓晓（女·edge）" },
    { v: "zh-CN-XiaoyiNeural", l: "晓伊（女·edge）" },
    { v: "zh-CN-YunxiNeural", l: "云希（男·edge）" },
    { v: "zh-CN-YunyangNeural", l: "云扬（男·edge）" },
    { v: "zh-CN-XiaomoNeural", l: "晓墨（女·edge）" },
    { v: "zh-CN-XiaohanNeural", l: "晓涵（女·edge）" },
    { v: "zh-CN-YunjianNeural", l: "云健（男·edge）" },
    { v: "zh-CN-XiaoxuanNeural", l: "晓萱（女·edge）" },
    { v: "zm_Yunzhe", l: "云哲（男·kokoro）" },
  ];

  s.loadRoles = async function() {
    const r = await API.roles();
    this.roles = r && r.data ? r.data.roles : [];
  };
  s.openRole = function(r) {
    const base = r ? JSON.parse(JSON.stringify(r)) : {
      id: "", name: "", gender: "女", is_primary: false, voice: "zh-CN-XiaoxiaoNeural",
      tts_backend: "edge-tts", presence_rooms: ["*"], output_devices: [], system: "", skills: [], enabled: true, fallback: "bark",
      wake_words: "", bound_rooms: "", scope: "family", member: "",
    };
    base.presence_rooms = (base.presence_rooms || ["*"]).join("、");
    base.skills = (base.skills || []).join("、");
    base.wake_words = (base.wake_words || []).join("、");
    base.bound_rooms = (base.bound_rooms || []).join("、");
    base.output_devices = Array.isArray(base.output_devices) ? base.output_devices : [];
    this.editingRole = base;
  };
  s.newRole = function() { this.openRole(null); };
  s.voiceTakenBy = function(voice) {
    if (!voice) return null;
    const cur = this.editingRole && this.editingRole.id;
    for (const r of this.roles) {
      if (r.id !== cur && r.voice === voice) return r.name;
    }
    return null;
  };
  s.saveRole = async function() {
    const r = this.editingRole;
    if (!r.id.trim()) return this.showToast("角色 id 必填");
    if (!r.name.trim()) return this.showToast("角色名称必填");
    const taker = this.voiceTakenBy(r.voice);
    if (taker) return this.showToast(`音色「${r.voice}」已被「${taker}」占用，请换一个`);
    if (typeof r.presence_rooms === "string") r.presence_rooms = r.presence_rooms.split(/[，,、]/).map(s => s.trim()).filter(Boolean);
    if (typeof r.skills === "string") r.skills = r.skills.split(/[，,、]/).map(s => s.trim()).filter(Boolean);
    if (typeof r.wake_words === "string") r.wake_words = r.wake_words.split(/[，,、]/).map(s => s.trim()).filter(Boolean);
    if (typeof r.bound_rooms === "string") r.bound_rooms = r.bound_rooms.split(/[，,、]/).map(s => s.trim()).filter(Boolean);
    if (r.scope !== "family" && r.scope !== "private") r.scope = "family";
    if (!Array.isArray(r.output_devices)) r.output_devices = [];
    this.loading = true;
    const res = await API.roleUpsert(r);
    this.loading = false;
    if (res && res.ok) { this.showToast("角色已保存"); this.editingRole = null; this.loadRoles(); this.loadDevices(); }
    else this.showToast((res && res.error) || "保存失败");
  };
  s.deleteRole = async function(id) {
    if (id === "butler") return this.showToast("主角色不可删除");
    const res = await API.roleDelete(id);
    this.showToast(res && res.ok ? "已删除" : "删除失败");
    this.loadRoles();
  };
  s.tryRole = async function(id) {
    const text = this.roleTryText || ("你好，我是" + ((this.roles.find(x => x.id === id) || {}).name || "管家"));
    const res = await API.roleTry(id, text);
    if (res && res.ok) {
      const dd = (res.data && res.data.dispatched) || [];
      const okc = dd.filter(x => x.ok).length;
      this.showToast(`已试跑：${okc}/${dd.length} 设备出声` + (res.data && res.data.provider ? `（${res.data.provider}）` : ""));
    } else this.showToast((res && res.error) || "试跑失败");
  };
  s.voiceTry = async function() {
    const r = this.editingRole;
    if (!r) return;
    if (r.tts_backend === "xiaoai") {
      const text = this.roleTryText || "这是小爱音箱直读试听。";
      const res = await API.ttsSpeak({ text, role: r.name, device_id: "xiao_study" });
      this.showToast(res && res.ok && res.data && res.data.spoken ? "小爱音箱已直读" : "小爱音箱不可用");
      return;
    }
    const v = r.voice;
    if (!v) return this.showToast("请选择音色");
    const res = await API.ttsTry(this.roleTryText || "这是一段音色试听。", "", v);
    this.showToast(res && res.ok && res.data && res.data.spoken ? "已合成播放" : "TTS 不可用");
  };
});
