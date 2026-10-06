const fs = require("fs");
const URL = "http://192.168.2.200:8000/mcp-white";
const TOKEN = "af_BbHo4EX5SdA9eYHASeMWuhbY4sOTYITY";
const FLOW = "d5a38c4777f84f35";
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
function trim(s, n) { s = (s || "").toString(); return s.length > n ? s.slice(0, n) + `\n...[truncated ${s.length - n} chars]` : s; }
(async () => {
  const init = await post({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "diag", version: "1.0" } } });
  const sid = init.nsid;
  await post({ jsonrpc: "2.0", method: "notifications/initialized" }, sid);

  const gf = await post({ jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: "autoflow_get_flow", arguments: { flow_id: FLOW, summary: false } } }, sid);
  const gfText = textOf(gf.body);
  fs.writeFileSync("e:/NAS/doubao-butler/tmp_butler_tab_raw.txt", gfText);
  let obj = null; try { obj = JSON.parse(gfText); } catch (e) {}
  console.log("=== GET_FLOW keys ===", obj ? Object.keys(obj) : "(parse fail)");
  let nodes = null;
  if (obj) { nodes = obj.nodes || obj.flow_json && obj.flow_json.nodes || (Array.isArray(obj.flow_json) ? obj.flow_json : null); }
  console.log("node count:", nodes ? nodes.length : "?");
  if (nodes) {
    console.log("=== NODE LIST ===");
    for (const n of nodes) {
      const w = JSON.stringify(n.wires || n.outputs || "");
      console.log(`${n.type}\t${n.id}\t"${(n.name || n.label || "")}"\twires=${w.slice(0,120)}`);
    }
    const flowObj = { id: FLOW, label: obj.label || "豆包管家", nodes };
    const s = JSON.stringify(flowObj);
    fs.writeFileSync("e:/NAS/doubao-butler/tmp_butler_tab_flow.json", s);

    const vf = await post({ jsonrpc: "2.0", id: 3, method: "tools/call", params: { name: "autoflow_validate_flow", arguments: { flow_json: s } } }, sid);
    const vfText = textOf(vf.body);
    fs.writeFileSync("e:/NAS/doubao-butler/tmp_butler_validate.txt", vfText);
    console.log("\n=== VALIDATE (summary) ===\n" + trim(vfText, 2500));

    const sf = await post({ jsonrpc: "2.0", id: 4, method: "tools/call", params: { name: "autoflow_simulate_flow", arguments: { flow_json: s } } }, sid);
    const sfText = textOf(sf.body);
    fs.writeFileSync("e:/NAS/doubao-butler/tmp_butler_simulate.txt", sfText);
    console.log("\n=== SIMULATE (summary) ===\n" + trim(sfText, 2500));
  }
  const nr = await post({ jsonrpc: "2.0", id: 5, method: "tools/call", params: { name: "autoflow_get_nr_flow", arguments: {} } }, sid);
  const nrText = textOf(nr.body);
  fs.writeFileSync("e:/NAS/doubao-butler/tmp_butler_nrflow.txt", nrText);
  console.log("\n=== GET_NR_FLOW (summary) ===\n" + trim(nrText, 1500));
  const dbg = await post({ jsonrpc: "2.0", id: 6, method: "tools/call", params: { name: "autoflow_debug_read", arguments: { flow_id: FLOW, limit: 20 } } }, sid);
  console.log("\n=== DEBUG_READ (summary) ===\n" + trim(textOf(dbg.body), 1000));
})();
