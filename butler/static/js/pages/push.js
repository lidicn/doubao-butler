/* 推送页逻辑 */
document.addEventListener("alpine:init", () => {
  const s = Alpine.store("app");
  // 推送表单状态
  s.nTitle = ""; s.nContent = ""; s.nType = "info"; s.nImportant = false;
  s.nDuration = 8; s.nTts = true; s.nVoice = ""; s.nVolume = 80; s.nPause = false; s.nMember = "";
  s.notifyHistory = [];
  s.notifyTemplates = [
    { title: "该喝水了", content: "已经坐了好久啦，起来喝口水活动一下吧～", type: "info" },
    { title: "该吃饭了", content: "到点啦，先把手头的事放一放，去吃饭咯", type: "info" },
    { title: "出门提醒", content: "记得带钥匙和手机，出门注意安全", type: "warning" },
    { title: "重要通知", content: "请尽快到客厅确认一下，有件事需要你处理", type: "error" },
  ];
  s.avatarFile = null; s.avatarFileName = ""; s.avatarTs = Date.now();

  s.loadNotifyHistory = async function() {
    const r = await API.notifyHistory(20);
    this.notifyHistory = r && r.data ? r.data.items : [];
  };
  s.applyTemplate = function(t) {
    this.nTitle = t.title; this.nContent = t.content; this.nType = t.type || "info";
  };
  s.pushNotify = async function() {
    if (!this.nTitle.trim() || !this.nContent.trim()) return this.showToast("标题和内容都必填");
    this.loading = true;
    const r = await API.notify(this.nTitle, this.nContent, {
      type: this.nType, important: this.nImportant,
      duration: this.nImportant ? 0 : this.nDuration * 1000,
      tts: this.nTts, volume: Number(this.nVolume),
      pause_media: this.nPause, voice: this.nVoice, member: this.nMember,
    });
    this.loading = false;
    if (r && r.ok) {
      this.showToast(r.data && r.data.status === "fail" ? "已发送（TV离线/失败）" : "已推送到电视");
      this.nTitle = ""; this.nContent = "";
      this.loadNotifyHistory();
    } else {
      this.showToast((r && r.error) || "推送失败");
    }
  };
  s.resendNotify = async function(item) {
    const r = await API.notify(item.title, item.content, {
      type: item.type, important: !!item.important,
      duration: item.important ? 0 : (item.duration || 8000),
      tts: !!item.with_tts, volume: Number(item.volume || 80),
      pause_media: !!item.pause_media, member: item.member,
    });
    this.showToast(r && r.ok ? "已重发" : "重发失败");
    this.loadNotifyHistory();
  };
  s.delNotify = async function(id) {
    await API.notifyDelete(id);
    this.loadNotifyHistory();
  };
  s.uploadAvatar = async function() {
    if (!this.avatarFile) return this.showToast("请先选择图片");
    const name = (this.nMember || "doubao").trim();
    this.loading = true;
    try {
      const r = await API.uploadAvatar(name, this.avatarFile);
      this.loading = false;
      if (r && r.ok) { this.avatarTs = Date.now(); this.showToast("头像已上传"); }
      else { this.showToast((r && r.error) || "上传失败"); }
    } catch (e) { this.loading = false; this.showToast("上传失败"); }
    this.avatarFile = null; this.avatarFileName = "";
  };

  // v1.8 PushGuard 风控面板
  s.guardData = null;
  s.loadGuardStatus = async function() {
    const r = await API.guardStatus();
    if (r && r.data) { this.guardData = r.data; }
  };

  // 推送管理：通道状态 + 技能推送开关
  s.pushChannels = null;
  s.pushSkills = null;
  s.loadPushChannels = async function() {
    const r = await API.pushChannels();
    if (r && r.data) { this.pushChannels = r.data; }
  };
  s.loadPushSkills = async function() {
    const r = await API.pushSkills();
    if (r && r.data) { this.pushSkills = r.data; }
  };
  s.togglePushSkill = async function(skillId, channel, enabled) {
    const r = await API.pushSkillUpdate(skillId, { [channel]: enabled });
    if (r && r.ok) {
      this.showToast(`${channel === 'bark' ? 'Bark' : channel === 'wechat' ? '微信' : '豆包app'} ${enabled ? '已启用' : '已禁用'}`);
      this.loadPushSkills();
    } else {
      this.showToast((r && r.error) || '操作失败');
    }
  };
});
