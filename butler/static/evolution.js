/* 自进化页逻辑 */
document.addEventListener("alpine:init", () => {
  const s = Alpine.store("app");
  s.evoDashboard = null; s.evoSuggestions = []; s.evoHistory = [];
  s.evoLoading = false; s.evoMode = "semi";
  s.evoTraces = []; s.evoStats = null;

  s.loadEvolution = async function() {
    this.evoLoading = true;
    try {
      const dash = await API.evolutionDashboard();
      this.evoDashboard = dash.data || dash;
      this.evoMode = (this.evoDashboard && this.evoDashboard.mode) || "semi";
    } catch (e) { console.error("dashboard failed", e); }
    try {
      const sug = await API.evolutionSuggestions();
      this.evoSuggestions = (sug.data && sug.data.items) || sug.data || [];
    } catch (e) { console.error("suggestions failed", e); }
    try {
      const hist = await API.evolutionHistory(20);
      this.evoHistory = (hist.data && hist.data.items) || [];
    } catch (e) { console.error("history failed", e); }
    try {
      const tr = await API.agentTraces(50);
      this.evoTraces = (tr.data && tr.data.traces) || tr.traces || [];
      // 统计
      const total = this.evoTraces.length;
      const fast = this.evoTraces.filter(t => t.llm_calls === 0).length;
      const llm = total - fast;
      this.evoStats = { total, fast, llm, hit_rate: total ? Math.round(fast/total*100) : 0 };
    } catch (e) { console.error("traces failed", e); }
    this.evoLoading = false;
  };
  s.runEvolutionAnalyze = async function() {
    if (this.evoLoading) return;
    this.evoLoading = true;
    try {
      const r = await API.evolutionAnalyze();
      await this.loadEvolution();
      this.toast(`分析完成，生成 ${(r.data && r.data.count) || 0} 条建议`, "success");
    } catch (e) { this.toast("分析失败", "error"); }
    finally { this.evoLoading = false; }
  };
  s.executeSuggestion = async function(id) {
    try {
      const r = await API.evolutionExecute(id);
      await this.loadEvolution();
      this.toast(r.data && r.data.ok ? "执行成功" : "执行失败", r.data && r.data.ok ? "success" : "error");
    } catch (e) { this.toast("执行失败", "error"); }
  };
  s.setEvolutionMode = async function(mode) {
    try {
      await API.evolutionSetMode(mode);
      this.evoMode = mode;
      this.toast(`已切换为${mode === "passive" ? "被动" : mode === "semi" ? "半自动" : "主动"}模式`, "success");
    } catch (e) { this.toast("切换失败", "error"); }
  };
});
