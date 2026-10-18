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
window.__ranking='skill';
render(data);
renderBoard('all');

const problems = [];
function checkExcluded(){
  for(const scope of ['all','s0','matched'])for(const ranking of ['pairwise','skill']){
    window.__pairwiseScope=scope;window.__ranking=ranking;renderBoard(scope==='s0'?'s0':'all');
    const body=els[ranking==='pairwise'?'pairwise-body':'lb-body'].innerHTML;
    for(const text of ['just4test','TEST ✓','Historical demo','Action ↗']){
      if(body.includes(text))problems.push(ranking+'/'+scope+' exposes '+text);
    }
  }
}
checkExcluded();

// The same row may hold one transparently retrospective real-round score. It
// gets the production score/loss columns and one round, but still no rank.
applyAgentProbes(data, {
  schema_version: 'ssa-agent-probes-v1',
  probes: [{
    kind: 'historical_demo', entrant_id: 'just4test', name: 'just4test',
    type: 'participant', status: 'passed',
    action_url: 'https://github.com/Social-Atoms/social-sim-arena/actions/runs/34279400481',
    round: {round_id: 'yougov-2026-w34-approval', target_type: 'continuous_normal',
            question: 'Economist/YouGov wave publishing ~Aug 18', unit: '% approve'},
    response: {schema_version: 'ssa-agent-api-v2', forecast: {mean: 34.5, sd: 1}},
    evaluation: {metric: 'CRPS', loss: 0.331403531254856,
                 persistence_loss: 1.280900969802875,
                 skill: 0.741273104582115, arena_score: 74.1273104582115,
                 outcome: 35, observed_date: '2026-08-15', rounds: 1,
                 retrospective: true},
  }],
});
window.__ranking='skill';
render(data);
renderBoard('all');
checkExcluded();
if (problems.length) {
  console.error(problems.join('\n'));
  process.exit(1);
}
console.log('endpoint probes and historical demos stay out of both leaderboard metrics');
