// 部署「豆包管家 客厅左 MVP」到 Node-RED tab d5a38c4777f84f35
// 复用 tmp_deploy_xb.js 已验证的鉴权(1880 /auth/token)与节点格式；自动探测 HA server 版本号。
const http = require('http');
const fs = require('fs');
const BASE = 'http://192.168.2.200:1880';
const USER = 'lidicn', PASS = 'longyin1003';
const TAB = 'd5a38c4777f84f35';
const BROKER = 'mvp_mqtt_broker';
const MQTT_USER = 'butler', MQTT_PASS = 'HPwXQWzBSavhOFfERoktdUy3';
const SPEAKER = 'notify.xiaomi_cn_266168894_lx06_execute_text_directive'; // 客厅左 小爱
const BUTLER = 'http://192.168.2.200:8095';

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
      resp.on('end', () => { let p; try { p = JSON.parse(ch); } catch (e) { p = ch; } res({ status: resp.statusCode, body: p }); });
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
  fs.writeFileSync('tmp_nr_backup_' + Date.now() + '.json', JSON.stringify(flows, null, 2));
  console.log('flows count', flows.length, '| backup saved');

  const server = (flows.find(n => n.type === 'server') || {}).id || 'e93e1ad9c034e866';
  console.log('HA server id =', server);

  const refTrigger = flows.find(n => n.type === 'trigger-state' || n.type === 'server-state-changed');
  const refApi = flows.find(n => n.type === 'api-call-service');
  const refEvents = flows.find(n => n.type === 'server-events');
  const trigVer = (refTrigger && refTrigger.version) || 5;
  const apiVer = (refApi && refApi.version) || 7;
  const evVer = (refEvents && refEvents.version) || 1;
  console.log('versions trigger=', trigVer, 'api=', apiVer, 'events=', evVer);

  // 1) 改写 /llm/chat 命中的 POST Chat API 节点 → butler /v1/chat/completions
  const postChat = flows.find(n => n.id === 'd68099f7f42078ce');
  if (!postChat) { console.error('POST Chat API not found'); process.exit(1); }
  postChat.url = BUTLER + '/v1/chat/completions';
  postChat.headers = [
    { key: 'Authorization', value: 'Bearer admin' },
    { key: 'Content-Type', value: 'application/json' }
  ];

  // 2) 新增 MVP 节点（前缀 mvp_，重跑时先去重）
  const newNodes = [
    { id: BROKER, type: 'mqtt-broker', name: 'mosquitto-butler', broker: '192.168.2.200', port: '1883',
      clientid: '', usetls: false, compatmode: false, keepalive: '60', cleansession: true,
      birthTopic: '', birthQos: '0', birthPayload: '', birthMsgType: 'utf8',
      willTopic: '', willQos: '0', willPayload: '', willMsgType: 'utf8',
      username: MQTT_USER, password: MQTT_PASS, userProps: '', sessionExpiry: '' },

    { id: 'mvp_comment', type: 'comment', z: TAB, name: '客厅左 豆包管家 MVP',
      info: '耳朵: 小爱 conversation 含「豆包」或按钮 30s 窗口内 → butler/event/voice\n按钮: 领普单击开窗 + TV 问候「我在，请说」\n嘴巴: butler/speak/out → 客厅左小爱念出',
      x: 120, y: 1180, wires: [] },

    // ---- 耳朵: 语音唤醒 ----
    { id: 'mvp_ear_ts', type: 'trigger-state', z: TAB, name: '客厅左小爱对话→耳朵', server, version: trigVer,
      inputs: 0, outputs: 1, exposeAsEntityConfig: '',
      entities: { entity: ['sensor.xiaomi_lx06_a137_conversation'], substring: [], regex: [] },
      debugEnabled: false, constraints: [], customOutputs: [], outputInitially: false,
      stateType: 'str', enableInput: false, wires: [['mvp_ear_fn']] },

    { id: 'mvp_ear_fn', type: 'function', z: TAB, name: '剥离唤醒词/窗口判断', outputs: 1, timeout: 0, noerr: 0,
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
        "if (/再见|退出|不用了|结束/.test(text) && armed) flow.set('mvp_armed_until', 0);\n" +
        "msg.payload = { member: '客厅左', text: text };\n" +
        "return msg;",
      wires: [['mvp_ear_mqtt']] },

    { id: 'mvp_ear_mqtt', type: 'mqtt out', z: TAB, name: '→butler/event/voice', broker: BROKER,
      topic: 'butler/event/voice', qos: '0', retain: 'false', respTopic: '', contentType: '',
      userProps: '', x: 980, y: 1240, wires: [] },

    // ---- 按钮唤醒 ----
    { id: 'mvp_btn_ev', type: 'server-events', z: TAB, name: '领普按钮单击', server, version: evVer,
      event_type: 'event.linp_cn_blt_3_1peaoqbmd0o00_ks2bb_click_e_5_1012',
      exposeAsEntityConfig: '', outputs: 1, wires: [['mvp_btn_fn']] },

    { id: 'mvp_btn_fn', type: 'function', z: TAB, name: '开30s窗口+TV问候', outputs: 1, timeout: 0, noerr: 0,
      initialize: '', finalize: '', libs: [],
      func:
        "flow.set('mvp_armed_until', Date.now() + 30000);\n" +
        "msg.payload = { title: '豆包管家', content: '我在，请说', tts: true };\n" +
        "msg.headers = { 'Authorization': 'Bearer admin', 'Content-Type': 'application/json' };\n" +
        "return msg;",
      wires: [['mvp_btn_http']] },

    { id: 'mvp_btn_http', type: 'http request', z: TAB, name: 'POST /api/notify (TV问候)', method: 'POST',
      ret: 'obj', paytoqs: 'ignore', url: BUTLER + '/api/notify', tls: '', persist: false, proxy: '',
      insecureHTTPParser: false, authType: '', senderr: false, headers: [], x: 980, y: 1360, wires: [['mvp_btn_dbg']] },

    { id: 'mvp_btn_dbg', type: 'debug', z: TAB, name: 'TV问候响应', active: false, tosidebar: true, console: false,
      tostatus: false, complete: 'payload', targetType: 'msg', statusVal: '', statusType: 'auto', x: 1180, y: 1360, wires: [] },

    // ---- 嘴巴: butler 回复 → 客厅左小爱 ----
    { id: 'mvp_mouth_mqtt', type: 'mqtt in', z: TAB, name: '←butler/speak/out', broker: BROKER,
      topic: 'butler/speak/out', qos: '0', datatype: 'json', x: 360, y: 1460, wires: [['mvp_mouth_fn']] },

    { id: 'mvp_mouth_fn', type: 'function', z: TAB, name: '取回复文本', outputs: 1, timeout: 0, noerr: 0,
      initialize: '', finalize: '', libs: [],
      func:
        "const text = (msg.payload && msg.payload.text) || '';\n" +
        "if (!text) return null;\n" +
        "msg.payload = text;\n" +
        "return msg;",
      wires: [['mvp_mouth_svc']] },

    { id: 'mvp_mouth_svc', type: 'api-call-service', z: TAB, name: '客厅左小爱念出', server, version: apiVer,
      debugenabled: false, action: 'notify.send_message', entityId: [SPEAKER],
      data: '{\"message\": payload}', dataType: 'jsonata', mergeContext: '', mustacheAltTags: false,
      outputProperties: [], queue: 'none', blockInputOverrides: true, domain: 'notify', service: 'send_message',
      wires: [[]] },
  ];

  flows = flows.filter(n => !String(n.id).startsWith('mvp_'));
  flows.push(...newNodes);

  const put = await req('POST', '/flows', flows, token);
  console.log('PUT /flows status', put.status);
  if (put.status !== 200) { console.error('PUT resp', JSON.stringify(put.body).slice(0, 400)); process.exit(1); }

  const vf = (await req('GET', '/flows', null, token)).body;
  const pc = vf.find(n => n.id === 'd68099f7f42078ce');
  console.log('VERIFY POST Chat API url =', pc && pc.url);
  console.log('VERIFY new nodes count =', vf.filter(n => String(n.id).startsWith('mvp_')).length);
  console.log('VERIFY ear trigger =', !!vf.find(n => n.id === 'mvp_ear_ts'),
              '| button events =', !!vf.find(n => n.id === 'mvp_btn_ev'),
              '| mouth mqtt =', !!vf.find(n => n.id === 'mvp_mouth_mqtt'));
  console.log('DONE');
})();
