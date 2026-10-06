// 本地修改 node-red-dev 的 flows.json：客厅左 豆包管家 MVP（全 HTTP，不走 MQTT）。
// 修复：耳朵必须用真正的 trigger-state 节点（用 entities 字段）；按钮删除冲突的 eventType。
const fs = require('fs');
const FLOW = 'tmp_nr_flows.json';
const OUT = 'tmp_nr_flows_out.json';
const TAB = 'd5a38c4777f84f35';
const SPEAKER = 'notify.xiaomi_cn_266168894_lx06_execute_text_directive_a_5_5'; // 客厅左 小爱（xiaomi_home 实体，含 _a_5_5 后缀）
const SPEAKER_MP = 'media_player.xiaomi_cn_266168894_lx06'; // 用于 media_stop 打断小爱原生播报
const BUTLER = 'http://192.168.2.200:8095';
const clone = o => JSON.parse(JSON.stringify(o));

let flows = JSON.parse(fs.readFileSync(FLOW, 'utf8'));
console.log('loaded nodes', flows.length);

const server = (flows.find(n => n.type === 'server') || {}).id || 'e93e1ad9c034e866';
const refTrigger = flows.find(n => n.id === 'b2e1c0ad86c99cce') || flows.find(n => n.type === 'trigger-state');
const refEvents = flows.find(n => n.type === 'server-events');
const refApi = flows.find(n => n.type === 'api-call-service');
if (!refTrigger || !refEvents || !refApi) {
  console.error('missing reference node', { refTrigger: !!refTrigger, refEvents: !!refEvents, refApi: !!refApi });
  process.exit(1);
}
console.log('server', server, '| earType', refTrigger.type, 'earVer', refTrigger.version, '| evVer', refEvents.version, '| apiVer', refApi.version);

// 1) /llm/chat 命中的 POST Chat API 节点 → butler /v1/chat/completions（架构校正）
const postChat = flows.find(n => n.id === 'd68099f7f42078ce');
if (postChat) {
  postChat.url = BUTLER + '/v1/chat/completions';
  postChat.headers = [
    { key: 'Authorization', value: 'Bearer admin' },
    { key: 'Content-Type', value: 'application/json' }
  ];
  console.log('rerouted /llm/chat ->', postChat.url);
} else {
  console.log('NOTE: POST Chat API node not found, skip reroute');
}

// 2) MVP 节点（前缀 mvp_，重跑先去重）
const comment = {
  id: 'mvp_comment', type: 'comment', z: TAB, name: '客厅左 豆包管家 MVP (HTTP)',
  info: '耳朵: 小爱 conversation 含「豆包」或按钮 30s 窗口内 → butler /v1/chat/completions → 客厅左小爱念出\n按钮: 领普单击开窗 + TV 问候「我在，请说」\n（全 HTTP，不走 MQTT——node-red-dev 到 mosquitto 1883 被拒）',
  wires: []
};

// 耳朵：语音唤醒（真正的 trigger-state 节点）
const earTs = clone(refTrigger);
earTs.id = 'mvp_ear_ts'; earTs.z = TAB; earTs.name = '客厅左小爱对话→耳朵'; earTs.server = server;
earTs.entities = { entity: ['sensor.xiaomi_lx06_a137_conversation'], substring: [], regex: [] };
// 显式固定输出：payload=实体状态字符串，data=事件对象（避免 undefined outputProperties 的默认行为不一致）
earTs.outputProperties = [
  { property: 'payload', propertyType: 'msg', value: 'entity', valueType: 'entityState' },
  { property: 'data', propertyType: 'msg', value: '', valueType: 'eventData' }
];
earTs.outputOnlyOnStateChange = true;
earTs.wires = [['mvp_ear_fn', 'mvp_ear_dbg']];

const earDbg = {
  id: 'mvp_ear_dbg', type: 'debug', z: TAB, name: '耳朵原始输入', active: true, tosidebar: true, console: false,
  tostatus: false, complete: 'true', targetType: 'msg', statusVal: '', statusType: 'auto', wires: []
};

const earFn = {
  id: 'mvp_ear_fn', type: 'function', z: TAB, name: '剥离唤醒词/窗口/上下文', outputs: 1, timeout: 0, noerr: 0,
  initialize: '', finalize: '', libs: [],
  func:
    "const raw = (msg.data && msg.data.event && msg.data.event.new_state && msg.data.event.new_state.state) || (typeof msg.payload==='string'?msg.payload:'');\n" +
    "if (!raw) return null;\n" +
    "const now = Date.now();\n" +
    "const armedUntil = flow.get('mvp_armed_until') || 0;\n" +
    "const armed = now < armedUntil;\n" +
    "if (!armed && !raw.includes('豆包')) return null;\n" +
    "let text = raw.replace(/豆包管家|小爱同学|豆包/g, '').trim();\n" +
    "if (!text) text = '你在吗';\n" +
    "if (raw.includes('豆包')) flow.set('mvp_history', []); // 新唤醒重置上下文\n" +
    "let history = flow.get('mvp_history') || [];\n" +
    "history.push({ role: 'user', content: text });\n" +
    "flow.set('mvp_history', history);\n" +
    "msg.payload = { model: 'doubao', messages: history };\n" +
    "return msg;",
  wires: [['mvp_ear_http']]
};

const earHttp = {
  id: 'mvp_ear_http', type: 'http request', z: TAB, name: 'butler /v1/chat/completions', method: 'POST',
  ret: 'obj', paytoqs: 'ignore', url: BUTLER + '/v1/chat/completions', tls: '', persist: false, proxy: '',
  insecureHTTPParser: false, authType: '', senderr: false, headers: [], wires: [['mvp_ear_reply']]
};

const earReply = {
  id: 'mvp_ear_reply', type: 'function', z: TAB, name: '提取回复/更新上下文', outputs: 1, timeout: 0, noerr: 0,
  initialize: '', finalize: '', libs: [],
  func:
    "const c = (msg.payload && msg.payload.choices && msg.payload.choices[0] && msg.payload.choices[0].message && msg.payload.choices[0].message.content) || '';\n" +
    "if (!c) return null;\n" +
    "let history = flow.get('mvp_history') || [];\n" +
    "history.push({ role: 'assistant', content: c });\n" +
    "if (history.length > 12) history = history.slice(-12);\n" +
    "flow.set('mvp_history', history);\n" +
    "msg.payload = c;\n" +
    "return msg;",
  wires: [['mvp_interrupt', 'mvp_reply_dbg']]
};

const replyDbg = {
  id: 'mvp_reply_dbg', type: 'debug', z: TAB, name: 'butler 回复', active: true, tosidebar: true, console: false,
  tostatus: false, complete: 'payload', targetType: 'msg', statusVal: '', statusType: 'auto', wires: []
};

// 打断：先停掉小爱当前原生播报（客厅左无 pause 按钮，用 media_player.media_stop）
const interruptSvc = clone(refApi);
interruptSvc.id = 'mvp_interrupt'; interruptSvc.z = TAB; interruptSvc.name = '打断小爱原生播报'; interruptSvc.server = server;
interruptSvc.action = 'media_player.media_stop'; interruptSvc.domain = 'media_player'; interruptSvc.service = 'media_stop';
interruptSvc.entityId = [SPEAKER_MP];
interruptSvc.data = ''; interruptSvc.dataType = 'json';
interruptSvc.wires = [['mvp_speak_delay']];

// 小延时让打断生效后再念出
const speakDelay = {
  id: 'mvp_speak_delay', type: 'delay', z: TAB, name: '打断后延时250ms', pauses: false,
  timeout: 250, timeoutUnits: 'milliseconds', rate: 1, nbRateUnits: 1, rateUnits: 'second',
  randomFirst: false, randomLast: false, randomFirstAmount: 1, randomLastAmount: 1,
  drop: false, outputs: 1, wires: [['mvp_mouth_svc']]
};

// 嘴巴：客厅左小爱念出（正确实体，含 _a_5_5 后缀）
const mouthSvc = clone(refApi);
mouthSvc.id = 'mvp_mouth_svc'; mouthSvc.z = TAB; mouthSvc.name = '客厅左小爱念出'; mouthSvc.server = server;
mouthSvc.action = 'notify.send_message'; mouthSvc.domain = 'notify'; mouthSvc.service = 'send_message';
mouthSvc.entityId = [SPEAKER];
mouthSvc.data = '{"message": payload}'; mouthSvc.dataType = 'jsonata';
mouthSvc.wires = [[]];

// 按钮：开窗 + TV 问候（删除克隆来源冲突的 eventType，只留 event_type）
const btnEv = clone(refEvents);
btnEv.id = 'mvp_btn_ev'; btnEv.z = TAB; btnEv.name = '领普按钮单击'; btnEv.server = server;
delete btnEv.eventType;
btnEv.event_type = 'event.linp_cn_blt_3_1peaoqbmd0o00_ks2bb_click_e_5_1012';
btnEv.wires = [['mvp_btn_fn']];

const btnFn = {
  id: 'mvp_btn_fn', type: 'function', z: TAB, name: '开30s窗口+TV问候', outputs: 1, timeout: 0, noerr: 0,
  initialize: '', finalize: '', libs: [],
  func:
    "flow.set('mvp_armed_until', Date.now() + 30000);\n" +
    "flow.set('mvp_history', []);\n" +
    "msg.payload = { title: '豆包管家', content: '我在，请说', tts: true };\n" +
    "msg.headers = { 'Authorization': 'Bearer admin', 'Content-Type': 'application/json' };\n" +
    "return msg;",
  wires: [['mvp_btn_http']]
};

const btnHttp = {
  id: 'mvp_btn_http', type: 'http request', z: TAB, name: 'POST /api/notify (TV问候)', method: 'POST',
  ret: 'obj', paytoqs: 'ignore', url: BUTLER + '/api/notify', tls: '', persist: false, proxy: '',
  insecureHTTPParser: false, authType: '', senderr: false, headers: [], wires: [['mvp_btn_dbg']]
};

const btnDbg = {
  id: 'mvp_btn_dbg', type: 'debug', z: TAB, name: 'TV问候响应', active: false, tosidebar: true, console: false,
  tostatus: false, complete: 'payload', targetType: 'msg', statusVal: '', statusType: 'auto', wires: []
};

const newNodes = [comment, earTs, earDbg, earFn, earHttp, earReply, replyDbg, interruptSvc, speakDelay, mouthSvc, btnEv, btnFn, btnHttp, btnDbg];

flows = flows.filter(n => !String(n.id).startsWith('mvp_'));
flows.push(...newNodes);

fs.writeFileSync(OUT, JSON.stringify(flows, null, 2));
console.log('written', OUT, '| total', flows.length, '| mvp nodes', flows.filter(n => String(n.id).startsWith('mvp_')).length);
