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
  return { nsid, status: r.status, body: parseSSE(text) };
}
(async () => {
  try {
    const init = await post({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "diag", version: "1.0" } } });
    console.log("INIT status", init.status, "session", init.nsid);
    const sid = init.nsid;
    await post({ jsonrpc: "2.0", method: "notifications/initialized" }, sid);
    const tl = await post({ jsonrpc: "2.0", id: 2, method: "tools/list" }, sid);
    console.log("TOOLS/LIST status", tl.status);
    const tools = (tl.body && tl.body.result && tl.body.result.tools) || [];
    console.log("TOOL COUNT", tools.length);
    for (const t of tools) {
      console.log(" -", t.name, "::", (t.description || "").slice(0, 80));
    }
  } catch (e) {
    console.error("ERR", e.message);
  }
})();
