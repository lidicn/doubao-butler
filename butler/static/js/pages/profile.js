/* 画像页逻辑 */
document.addEventListener("alpine:init", () => {
  const s = Alpine.store("app");
  s.profMembers = []; s.profName = ""; s.profData = null; s.profSaving = false;
  s.orgOpen = false; s.orgText = ""; s.orgDraft = null; s.orgLoading = false;

  s.loadProfiles = async function() {
    const r = await API.profileMembers();
    this.profMembers = r && r.data ? r.data.items : [];
    if (!this.profName && this.profMembers.length) this.profName = this.profMembers[0].name;
    if (this.profName) this.loadProfile(this.profName);
  };
  s.loadProfile = async function(name) {
    this.profName = name;
    const r = await API.profileGet(name);
    if (!r || !r.data) { this.profData = null; return; }
    this.profData = r.data;
    const p = (this.profData.member && this.profData.member.profile) || {};
    this.profData._draft = JSON.parse(JSON.stringify(p));
    this.profData._interestsText = (p.interests || []).join("、");
    this.profData._habitsText = (p.habits || []).join("、");
  };
  s.draft = function() {
    if (!this.profData) return null;
    const d = this.profData._draft || {};
    if (!Array.isArray(d.routine)) d.routine = [];
    if (!Array.isArray(d.courses)) d.courses = [];
    this.profData._draft = d;
    return d;
  };
  s.addRoutine = function() { const d = this.draft(); if (d) d.routine.push({ label: "", time: "", note: "" }); };
  s.delRoutine = function(i) { const d = this.draft(); if (d) d.routine.splice(i, 1); };
  s.addCourse = function() { const d = this.draft(); if (d) d.courses.push({ day: "周一", time: "", name: "", note: "" }); };
  s.delCourse = function(i) { const d = this.draft(); if (d) d.courses.splice(i, 1); };
  s._splitTags = function(str) { return (str || "").split(/[，,、]/).map(x => x.trim()).filter(Boolean); };
  s.saveProfile = async function() {
    const d = this.draft();
    if (!this.profName || !d) return this.showToast("未选择成员");
    d.interests = this._splitTags(this.profData._interestsText);
    d.habits = this._splitTags(this.profData._habitsText);
    this.profSaving = true;
    const res = await API.profilePut(this.profName, d);
    this.profSaving = false;
    this.showToast(res && res.ok ? "画像已写回" : ((res && res.error) || "写回失败"));
    if (res && res.ok) this.loadProfile(this.profName);
  };
  s.revokeObs = async function(id) {
    const res = await API.profileRevoke(id);
    this.showToast(res && res.ok ? "已撤销该观察" : ((res && res.error) || "撤销失败"));
    this.loadProfile(this.profName);
  };
  s.openOrganize = function() { this.orgOpen = true; this.orgText = ""; this.orgDraft = null; };
  s.runOrganize = async function() {
    if (!this.orgText.trim()) return this.showToast("请先粘贴一段描述");
    this.orgLoading = true;
    const res = await API.profileOrganize(this.profName, this.orgText);
    this.orgLoading = false;
    if (res && res.ok) this.orgDraft = res.data.profile;
    else this.showToast((res && res.error) || "LLM 整理失败");
  };
  s.applyOrganize = function() {
    if (!this.orgDraft || !this.profData) return;
    this.profData._draft = JSON.parse(JSON.stringify(this.orgDraft));
    const p = this.orgDraft;
    this.profData._interestsText = (p.interests || []).join("、");
    this.profData._habitsText = (p.habits || []).join("、");
    this.orgOpen = false;
    this.showToast("已填入左侧表单，确认后点「保存写回」");
  };
});
