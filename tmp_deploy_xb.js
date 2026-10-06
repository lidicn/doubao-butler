const http = require('http');
const fs = require('fs');
const BASE = 'http://192.168.2.200:1880';
const USER = 'lidicn', PASS = 'longyin1003';

function req(method, path, body, token, form) {
  return new Promise((res, rej) => {
    let data = body ? (form ? body : JSON.stringify(body)) : null;
    const headers = {};
    if (data) headers['Content-Type'] = form ? 'application/x-www-form-urlencoded' : 'application/json';
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const u = new URL(BASE + path);
    const r = http.request({ hostname: u.hostname, port: u.port, path: u.pathname + u.search, method, headers }, resp => {
      let ch = '';
      resp.on('data', d => ch += d);
      resp.on('end', () => {
        let parsed;
        try { parsed = JSON.parse(ch); } catch (e) { parsed = ch; }
        res({ status: resp.statusCode, body: parsed });
      });
    });
    r.on('error', rej);
    if (data) r.write(data);
    r.end();
  });
}

(async () => {
  const tok = await req('POST', '/auth/token',
    `grant_type=password&client_id=node-red-editor&client_secret=not_available&username=${USER}&password=${PASS}`, null, true);
  const token = tok.body.access_token;
  if (!token) { console.error('NO TOKEN', tok); process.exit(1); }
  console.log('token ok');

  const get = await req('GET', '/flows', null, token);
  let flows = get.body;
  if (!Array.isArray(flows)) { console.error('flows not array', get.status, JSON.stringify(get.body).slice(0, 200)); process.exit(1); }
  fs.writeFileSync(process.env.TEMP + '\\nr_backup_' + Date.now() + '.json', JSON.stringify(flows, null, 2));
  console.log('flows count', flows.length, '| backup saved');

  const postChat = flows.find(n => n.id === 'd68099f7f42078ce');
  if (!postChat) { console.error('POST Chat API not found'); process.exit(1); }
  postChat.url = 'http://192.168.2.200:8095/v1/chat/completions';
  postChat.headers = { 'Authorization': 'Bearer admin', 'Content-Type': 'application/json' };

  const callHub = flows.find(n => n.id === 'b1466c9954bc6a07');
  if (!callHub) { console.error('调用中枢 /llm/chat not found'); process.exit(1); }
  callHub.url = 'http://192.168.2.200:1880/llm/chat';

  const SERVER = 'e93e1ad9c034e866';
  const tabId = 'tab_xb_001', tsId = 'ts_xb_001', fnEx = 'fn_xb_extract',
        httpId = 'http_xb_001', fnRep = 'fn_xb_reply', svcId = 'svc_xb_tts';

  const http_xb = JSON.parse(JSON.stringify(postChat));
  http_xb.id = httpId; http_xb.name = '调用 butler'; http_xb.z = tabId;
  http_xb.wires = [[fnRep]];
  http_xb.url = 'http://192.168.2.200:8095/v1/chat/completions';
  http_xb.headers = { 'Authorization': 'Bearer admin', 'Content-Type': 'application/json' };

  const newNodes = [
    { id: tabId, type: 'tab', label: '小爱对话→butler', disabled: false, info: '' },
    {
      id: tsId, type: 'trigger-state', z: tabId, name: '小爱对话→butler', server: SERVER, version: 5,
      inputs: 0, outputs: 1, exposeAsEntityConfig: '',
      entities: { entity: ['sensor.xiaomi_lx06_a137_conversation', 'sensor.xiaomi_lx06_7709_conversation'], substring: [], regex: [] },
      debugEnabled: false, constraints: [], customOutputs: [], outputInitially: false, stateType: 'str', enableInput: false,
      wires: [[fnEx]]
    },
    {
      id: fnEx, type: 'function', z: tabId, name: '提取文本+剥离唤醒词', outputs: 1, timeout: 0, noerr: 0, initialize: '', finalize: '', libs: [],
      func:
        "let raw=(msg.data&&msg.data.event&&msg.data.event.new_state&&msg.data.event.new_state.state)||(typeof msg.payload==='string'?msg.payload:'');\n" +
        "if(!raw)return null;\n" +
        "const WAKE='豆包管家';\n" +
        "if(!raw.includes(WAKE))return null;\n" +
        "let text=raw.replace(WAKE,'').trim();\n" +
        "if(!text)text='你在吗';\n" +
        "msg.speaker=(msg.data&&msg.data.entity_id)||'unknown';\n" +
        "msg.payload={model:'butler',messages:[{role:'user',content:text}]};\n" +
        "return msg;",
      wires: [[httpId]]
    },
    http_xb,
    {
      id: fnRep, type: 'function', z: tabId, name: '提取回复', outputs: 1, timeout: 0, noerr: 0, initialize: '', finalize: '', libs: [],
      func:
        "let c=msg.payload&&msg.payload.choices&&msg.payload.choices[0]&&msg.payload.choices[0].message&&msg.payload.choices[0].message.content;\n" +
        "if(!c)c='（抱歉，我暂时没想好怎么回）';\n" +
        "msg.payload=c;\n" +
        "return msg;",
      wires: [[svcId]]
    },
    {
      id: svcId, type: 'api-call-service', z: tabId, name: '小爱念出', server: SERVER, version: 7, debugenabled: false,
      action: 'notify.send_message', entityId: ['notify.xiaomi_cn_330794773_x08a_execute_text_directive_a_5_4'],
      data: '{"message": payload}', dataType: 'jsonata', mergeContext: '', mustacheAltTags: false, outputProperties: [],
      queue: 'none', blockInputOverrides: true, domain: 'notify', service: 'send_message', wires: [[]]
    },
  ];
  flows.push(...newNodes);

  const put = await req('POST', '/flows', flows, token);
  console.log('PUT /flows status', put.status);
  console.log('PUT resp', JSON.stringify(put.body).slice(0, 300));

  if (put.status === 200) {
    const verify = await req('GET', '/flows', null, token);
    const vf = verify.body;
    const pc = vf.find(n => n.id === 'd68099f7f42078ce');
    const ch = vf.find(n => n.id === 'b1466c9954bc6a07');
    const ts = vf.find(n => n.id === tsId);
    console.log('VERIFY POST Chat API url =', pc && pc.url);
    console.log('VERIFY 调用中枢 url =', ch && ch.url);
    console.log('VERIFY new tab 小爱对话→butler present =', !!vf.find(n => n.id === tabId));
    console.log('VERIFY new trigger present =', !!ts, '| nodes total =', vf.length);
  }
})();
