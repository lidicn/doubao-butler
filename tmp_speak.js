const fs = require('fs');
const f = JSON.parse(fs.readFileSync('tmp_nr_flows_out.json', 'utf8'));
const byId = id => f.find(n => n.id === id);

console.log('=== nodes mentioning 左音箱/双音箱/唤醒/打断/stop (all tabs) ===');
f.filter(n => /左音箱|双音箱|唤醒|打断|interrupt|cancel|stop/i.test(n.name || '')).forEach(n => {
  console.log([n.type, n.id, n.name, 'z=' + n.z, 'wires=' + JSON.stringify(n.wires)].join(' | '));
});

console.log('\n=== all api-call-service that target 小爱 (execute_text_directive / xiaomi) ===');
f.filter(n => n.type === 'api-call-service' && /xiaomi|execute_text_directive|notify/i.test(JSON.stringify(n.entityId || '') + (n.name||''))).forEach(n => {
  console.log([n.id, n.name, 'action=' + n.action, 'entityId=' + JSON.stringify(n.entityId), 'wires=' + JSON.stringify(n.wires)].join(' | '));
});

console.log('\n=== trace: who feeds/wires from mvp_mouth_svc and the 左音箱 speaker node ===');
// find speaker nodes by entityId containing execute_text_directive
const spk = f.filter(n => n.type === 'api-call-service' && /execute_text_directive/i.test(JSON.stringify(n.entityId||'')));
spk.forEach(s => {
  const upstream = f.filter(n => JSON.stringify(n.wires||[]).includes(s.id)).map(n => n.id + ':' + n.name + '(' + n.type + ')');
  console.log('SPEAKER', s.id, s.name, 'entityId=' + JSON.stringify(s.entityId));
  console.log('  upstream ->', upstream.join(', ') || '(none)');
});
