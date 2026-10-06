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
  return { nsid, body: parseSSE(await r.text()) };
}
function textOf(res) {
  const c = res && res.result && res.result.content || [];
  return c.map(x => x.text || "").join("");
}
(async () => {
  const init = await post({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "diag", version: "1.0" } } });
  const sid = init.nsid;
  await post({ jsonrpc: "2.0", method: "notifications/initialized" }, sid);
  const tl = await post({ jsonrpc: "2.0", id: 2, method: "tools/list" }, sid);
  const tools = (tl.body.result && tl.body.result.tools) || [];
  console.log("=== FLOW-RELATED TOOL SCHEMAS ===");
  for (const t of tools) {
    if (/flow|validate|simulate|verify|nr_flow|debug_read|get_flow/i.test(t.name)) {
      console.log("\n## " + t.name);
      console.log("desc:", (t.description || "").slice(0, 120));
      console.log("schema:", JSON.stringify(t.inputSchema && t.inputSchema.properties || {}, null, 1).slice(0, 800));
    }
  }
})();
