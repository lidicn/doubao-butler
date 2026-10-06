const fs = require('fs');
const FLOW = process.env.FLOW || '/data/flows.json';
const flows = JSON.parse(fs.readFileSync(FLOW, 'utf8'));
if (!Array.isArray(flows)) { console.error('flows not array'); process.exit(1); }
console.log('loaded flows count', flows.length);

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

const existing = new Set(flows.map(n => n.id));
const clash = newNodes.filter(n => existing.has(n.id));
if (clash.length) { console.error('ID CLASH', clash.map(n => n.id)); process.exit(1); }
flows.push(...newNodes);

fs.writeFileSync(FLOW, JSON.stringify(flows, null, 2));
console.log('written. new count', flows.length);
console.log('postChat url=', postChat.url);
console.log('callHub url=', callHub.url);
console.log('new tab present=', !!flows.find(n => n.id === tabId));
