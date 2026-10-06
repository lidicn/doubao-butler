const fs = require('fs');
const f = JSON.parse(fs.readFileSync('tmp_nr_flows_out.json', 'utf8'));
const TAB = '57be9a8f1fca2bcd';
console.log('=== tab 57be9a8f1fca2bcd (existing 左/右/双音箱 flow) ===');
f.filter(n => n.z === TAB).forEach(n => {
  console.log([n.type, n.id, n.name || '', 'wires=' + JSON.stringify(n.wires)].join(' | '));
});
console.log('\n=== all button.* pause/stop entities in whole flows ===');
f.filter(n => n.type === 'api-call-service' && /button\.|pause|stop|media_player\.media_stop/i.test((n.action||'') + JSON.stringify(n.entityId||''))).forEach(n => {
  console.log([n.id, n.name, 'action=' + n.action, 'entityId=' + JSON.stringify(n.entityId)].join(' | '));
});
console.log('\n=== entities containing 266168894 (客厅左 device) anywhere ===');
f.filter(n => /266168894/.test(JSON.stringify(n))).forEach(n => {
  console.log([n.type, n.id, n.name || '', JSON.stringify(n.entityId||'')].join(' | '));
});
