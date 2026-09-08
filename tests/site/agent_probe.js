// A passed endpoint probe belongs beside the leaderboard, but it is not a
// scored round. Execute the shipped page code and require that distinction in
// the actual row a visitor sees.
const fs = require('fs');
const path = require('path');
const {installGlobals} = require('./dom_stub.js');

const root = path.resolve(__dirname, '..', '..');
const html = fs.readFileSync(path.join(root, 'site', 'index.html'), 'utf8');
const script = (html.match(/<script>([\s\S]*?)<\/script>/g) || [])
  .map(b => b.replace(/^<script>/, '').replace(/<\/script>$/, '')).join('\n');
const {els} = installGlobals();
const data = JSON.parse(fs.readFileSync(path.join(root, 'site', 'data.json'), 'utf8'));

eval(script);
applyAgentProbes(data, {
  schema_version: 'ssa-agent-probes-v1',
  updated_at: '2026-09-08T17:00:00Z',
  probes: [{
    entrant_id: 'just4test', name: 'just4test', type: 'participant',
    status: 'passed', tested_at: '2026-09-08T17:00:00Z',
    action_url: 'https://github.com/Social-Atoms/social-sim-arena/actions/runs/34279400481',
    response: {schema_version: 'ssa-agent-api-v2', forecast: {mean: 50, sd: 1}},
  }],
});
render(data);
renderBoard('all');

const body = els['lb-body'].innerHTML;
const note = els['lb-note'].textContent;
const row = body.split('</tr>').find(x => x.includes('just4test')) || '';
const problems = [];
for (const want of ['just4test', 'TEST ✓', 'Participant', 'API test passed',
                    'μ 50', 'σ 1', 'actions/runs/34279400481']) {
  if (!row.includes(want)) problems.push(`test row is missing ${want}`);
}
if (!row.includes('<td class="rank">·</td>')) problems.push('test row received a rank');
if (!row.endsWith('<td class="num">·</td><td class="num">0</td>')) {
  problems.push('test row received a loss or a scored round');
}
if (!note.includes('non-scored endpoint check')) {
  problems.push('leaderboard does not explain that TEST is non-scored');
}
if (!body.startsWith('<tr class="probe-row"')) {
  problems.push('the freshly tested participant is not immediately visible at the top');
}
if (problems.length) {
  console.error(problems.join('\n'));
  process.exit(1);
}
console.log('just4test renders beside the leaderboard as TEST ✓, μ 50, σ 1, 0 rounds');
