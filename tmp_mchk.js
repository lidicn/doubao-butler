const fs = require('fs');
const f = JSON.parse(fs.readFileSync('tmp_nr_flows_out.json', 'utf8'));
const g = id => f.find(n => n.id === id);
console.log('mouth:', JSON.stringify(g('mvp_mouth_svc').entityId), '| action', g('mvp_mouth_svc').action, '| data', g('mvp_mouth_svc').data);
console.log('interrupt:', JSON.stringify(g('mvp_interrupt').entityId), '| action', g('mvp_interrupt').action, '| wires', JSON.stringify(g('mvp_interrupt').wires));
console.log('delay:', g('mvp_speak_delay').type, '| timeout', g('mvp_speak_delay').timeout, g('mvp_speak_delay').timeoutUnits, '| wires', JSON.stringify(g('mvp_speak_delay').wires));
console.log('earReply wires:', JSON.stringify(g('mvp_ear_reply').wires));
