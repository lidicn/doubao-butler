const fs = require('fs');
const f = JSON.parse(fs.readFileSync('tmp_nr_flows_out.json', 'utf8'));
const TAB = 'd5a38c4777f84f35';
const nodes = f.filter(n => n.z === TAB);
console.log('=== tab', TAB, 'nodes:', nodes.length, '===');
nodes.forEach(n => {
  const name = n.name || (n.type === 'comment' ? n.info && n.info.slice(0,30) : '');
  console.log([n.type, n.id, JSON.stringify(name).slice(0,40), 'wires=' + JSON.stringify(n.wires)].join(' | '));
});
console.log('=== search keywords: 打断/stop/interrupt/唤醒/左音箱/双音箱/wake ===');
nodes.filter(n => /打断|stop|interrupt|唤醒|左音箱|双音箱|wake|cancel/i.test(JSON.stringify(n))).forEach(n => {
  console.log('>>>', n.type, n.id, n.name || '', JSON.stringify(n).slice(0,300));
});
