const fs = require('fs');
const f = JSON.parse(fs.readFileSync('tmp_nr_flows_out.json', 'utf8'));
const byId = id => f.find(n => n.id === id);

// link out 入口（来自既有 tab）
const outs = f.filter(n => n.type === 'link out');
console.log('=== link out nodes ===');
outs.forEach(n => console.log([n.id, n.name || '', 'z=' + n.z].join(' | ')));

// 找对应的 link in（id 相同）并向下追踪打断+念出
const queueIds = ['14d42e0503c9d959', 'ab13b34ec8c914e5', 'a5637c5db0f86e30'];
queueIds.forEach(qid => {
  const li = byId(qid);
  if (!li) { console.log('\n[link in ' + qid + ' NOT FOUND]'); return; }
  console.log('\n=== queue link in ' + qid + ' (' + (li.name||'') + ') z=' + li.z + ' ===');
  // BFS downstream up to 6 hops
  const seen = new Set(); let frontier = [li.id];
  for (let hop = 0; hop < 6; hop++) {
    const next = [];
    frontier.forEach(id => {
      if (seen.has(id)) return; seen.add(id);
      const n = byId(id); if (!n) return;
      const tag = [n.type, n.id, n.name || '', (n.action||''), JSON.stringify(n.entityId||'')].filter(Boolean).join(' | ');
      console.log('  '.repeat(hop) + '• ' + tag);
      (n.wires || []).forEach(arr => arr.forEach(t => next.push(t)));
    });
    frontier = [...new Set(next)];
    if (!frontier.length) break;
  }
});

console.log('\n=== media_player / pause for 客厅左 (266168894) anywhere ===');
f.filter(n => /266168894/.test(JSON.stringify(n)) && /media_player|button|pause|stop/i.test(JSON.stringify(n))).forEach(n => {
  console.log([n.type, n.id, n.name || '', (n.action||''), JSON.stringify(n.entityId||'')].join(' | '));
});
