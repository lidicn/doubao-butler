const URL = "http://192.168.2.200:8000/mcp-white";
const TOKEN = "af_BbHo4EX5SdA9eYHASeMWuhbY4sOTYITY";
function parseSSE(text) {
  let last = null;
  for (const line of text.split("\n")) {
    const l = line.trim();
    if (l.startsWith("data:")) last = l.slice(5).trim();
  }
  try { return JSON.parse(last || text); } catch (e) { return { raw: text }; }
}
async function post(body, sid) {
  const h = { Authorization: "Bearer " + TOKEN, "Content-Type": "application/json", Accept: "application/json, text/event-stream" };
  if (sid) h["Mcp-Session-Id"] = sid;
  const r = await fetch(URL, { method: "POST", headers: h, body: JSON.stringify(body) });
  const nsid = r.headers.get("mcp-session-id") || r.headers.get("Mcp-Session-Id");
  const text = await r.text();
  return { nsid, body: parseSSE(text) };
}
function textOf(res) {
  const c = res && res.result && res.result.content || [];
  return c.map(x => x.text || "").join("");
}
(async () => {
  const init = await post({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "diag", version: "1.0" } } });
  const sid = init.nsid;
  await post({ jsonrpc: "2.0", method: "notifications/initialized" }, sid);
  const tl = await post({ jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: "autoflow_list_tabs", arguments: {} } }, sid);
  console.log("=== LIST TABS ===");
  console.log(textOf(tl.body).slice(0, 1500));
  const gf = await post({ jsonrpc: "2.0", id: 3, method: "tools/call", params: { name: "autoflow_get_flow", arguments: { flow_id: "d5a38c4777f84f35" } } }, sid);
  console.log("=== GET FLOW d5a38c4777f84f35 ===");
  console.log(textOf(gf.body).slice(0, 1500));
})();
