/* 设备页逻辑（本地设备 + HA 设备管理） */
document.addEventListener("alpine:init", () => {
  const s = Alpine.store("app");
  s.devices = []; s.editingDevice = null;
  // HA 设备管理状态（v1.4）
  s.haDomains = []; s.haDevices = {}; s.haTotal = 0;
  s.haLoading = false; s.haDomainFilter = ""; s.haSearch = ""; s.haOnlineOnly = false;

  s.loadDevices = async function() {
    const r = await API.devices();
    this.devices = r && r.data ? r.data.devices : [];
  };
  s.openDevice = function(d) {
    this.editingDevice = d ? JSON.parse(JSON.stringify(d)) : {
      id: "", type: "xiaomi", room: "", ha_entity: "", mqtt: "", channel: "", voice_override: "", enabled: true,
      play_mode: "notify_text", ha_tts_entity: "", ha_player_entity: "", ha_sensor: "",
    };
  };
  s.newDevice = function() { this.openDevice(null); };
  s.saveDevice = async function() {
    const d = this.editingDevice;
    if (!d.id.trim()) return this.showToast("设备 id 必填");
    this.loading = true;
    const res = await API.deviceUpsert(d);
    this.loading = false;
    if (res && res.ok) { this.showToast("设备已保存"); this.editingDevice = null; this.loadDevices(); }
    else this.showToast((res && res.error) || "保存失败");
  };
  s.deleteDevice = async function(id) {
    const res = await API.deviceDelete(id);
    this.showToast(res && res.ok ? "已删除" : "删除失败");
    this.loadDevices();
  };
  // HA 设备管理
  s.loadHaDevices = async function() {
    this.haLoading = true;
    try {
      const params = {};
      if (this.haDomainFilter) params.domain = this.haDomainFilter;
      if (this.haSearch) params.search = this.haSearch;
      if (this.haOnlineOnly) params.online_only = "true";
      const res = await API.haDevices(params);
      const d = res.data || res;
      this.haDevices = d.domains || {};
      this.haTotal = d.total || 0;
    } catch (e) { console.error("loadHaDevices failed", e); }
    finally { this.haLoading = false; }
  };
  s.loadHaDomains = async function() {
    try {
      const res = await API.haDomains();
      this.haDomains = (res.data || res).domains || [];
    } catch (e) { console.error("loadHaDomains failed", e); }
  };
});
