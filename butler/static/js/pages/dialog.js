/* 对话页 + 人格页逻辑 */
document.addEventListener("alpine:init", () => {
  const s = Alpine.store("app");
  s.roleTryText = "";

  s.testSpeak = async function() {
    const text = (this.$refs && this.$refs.testText && this.$refs.testText.value) || "";
    const member = (this.$refs && this.$refs.testMember && this.$refs.testMember.value) || "";
    if (!text) return this.showToast("请输入测试文本");
    this.loading = true;
    const r = await API.dialogTest(member, text);
    this.loading = false;
    if (r && r.ok) this.showToast(r.data && r.data.spoken ? "已播报" : "已处理（未发声）");
    else this.showToast((r && r.error) || "失败");
    this.loadRecent();
  };
  s.ttsTry = async function() {
    const text = (this.$refs && this.$refs.ttsText && this.$refs.ttsText.value) || "";
    if (!text) return this.showToast("请输入试听文本");
    const r = await API.ttsTry(text, "", this.tts && this.tts.edge_voice);
    if (r && r.ok) this.showToast(r.data && r.data.spoken ? "已合成并在电视播放" : "TTS 不可用");
    else this.showToast("TTS 失败");
  };
  // 人格页
  s.savePersona = async function() {
    const r = await API.savePersona(this.config.system, this.config.greeting_template);
    this.showToast(r && r.ok ? "人格已保存" : "保存失败");
  };
  s.saveWakeup = async function() {
    const r = await API.saveWakeup({
      cooldown_seconds: Number(this.config.cooldown_seconds),
      dnd_windows: (this.config.dnd_windows || []),
    });
    this.showToast(r && r.ok ? "唤醒设置已保存" : "保存失败");
  };
});
