const URL = "http://192.168.2.200:8000/mcp-white";
const TOKEN = "af_BbHo4EX5SdA9eYHASeMWuhbY4sOTYITY";
function parseSSE(text) {
  let last = null;
  for (const line of text.split("\n")) { const l = line.trim(); if (l.startsWith("data:")) last = l.slice(5).trim(); }
  try { return JSON.parse(last || text); } catch (e) { return { raw: text }; }
}
async function post(body, sid) {
  const h = { Authorization: "Bearer " + TOKEN, "Content-Type": "application/json", Accept: "application/json, text/event-stream" };
  if (sid) h["Mcp-Session-Id"] = sid;
  const r = await fetch(URL, { method: "POST", headers: h, body: JSON.stringify(body) });
  const nsid = r.headers.get("mcp-session-id") || r.headers.get("Mcp-Session-Id");
  return { nsid, body: parseSSE(await r.text()) };
}
function textOf(res) { const c = res && res.result && res.result.content || []; return c.map(x => x.text || "").join(""); }
async function call(name, args, sid) {
  const r = await post({ jsonrpc: "2.0", id: Math.floor(Math.random()*1e6), method: "tools/call", params: { name, arguments: args } }, sid);
  return textOf(r.body);
}
(async () => {
  const init = await post({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "diag", version: "1.0" } } });
  const sid = init.nsid;
  await post({ jsonrpc: "2.0", method: "notifications/initialized" }, sid);
  for (const kw of ["小爱", "音箱", "客厅", "linp", "asr", "conversation", "speaker"]) {
    const out = await call("autoflow_list_entities", { keyword: kw, limit: 40 }, sid);
    console.log("\n===== keyword: " + kw + " =====");
    console.log(out.slice(0, 2200));
  }
})();
