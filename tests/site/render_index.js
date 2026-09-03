// Runs site/index.html's own script against a real data.json. The landing page
// is the first thing a participant sees; this asserts it renders, that the
// open-round list names each round's answer shape, and that every date it puts
// in front of a participant is the deadline they are held to rather than the
// arena's later internal lock.
const fs = require('fs');
const path = require('path');
const {installGlobals} = require('./dom_stub.js');

const root = path.resolve(__dirname, '..', '..');
const dataPath = process.argv[2] || path.join(root, 'site', 'data.json');
const html = fs.readFileSync(path.join(root, 'site', 'index.html'), 'utf8');
const script = (html.match(/<script>([\s\S]*?)<\/script>/g) || [])
  .map(b => b.replace(/^<script>/, '').replace(/<\/script>$/, '')).join('\n');

const {els} = installGlobals();

const data = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
const problems = [];
eval(script);
render(data);

const list = els['rounds-list'].innerHTML;
const exam = els['exam-rounds'].innerHTML;
if (!list) problems.push('the open-round list rendered nothing');

// Every shaped round the page shows must say what shape it is.
const shaped = data.rounds.filter(r =>
  r.target_type === 'profile_energy' || r.target_type === 'ranking_list');
const shownShaped = shaped.filter(r => list.includes(r.round_id));
for (const r of shownShaped) {
  const row = list.split('<div class="vrow">')
    .find(chunk => chunk.includes(r.round_id)) || '';
  if (!/class="shape"/.test(row)) {
    problems.push(`${r.round_id} is a ${r.target_type} and its row does not `
      + 'say so; the page presents it as one number');
  }
}

// No list may put the arena's internal lock in front of a participant as the
// moment they are due. Only `deadline` (or `lock_at` before the cutover) may
// carry the word.
for (const [name, body] of [['open rounds', list], ['midterm rounds', exam]]) {
  if (/>locks /.test(body)) {
    problems.push(`the ${name} list labels a date "locks"; participants are `
      + 'held to the batch deadline, which is earlier');
  }
}

console.log(`open-round rows : ${(list.match(/class="vrow"/g) || []).length}`);
console.log(`midterm rows    : ${(exam.match(/class="vrow"/g) || []).length}`);
console.log(`shaped rounds   : ${shaped.length} in data, ${shownShaped.length} on the page, `
  + `${(list.match(/class="shape"/g) || []).length} chips rendered`);
console.log(`footer          : ${els['foot-updated'].textContent}`);

if (problems.length) {
  console.error('\n' + problems.length + ' problem(s):');
  for (const p of problems) console.error('  - ' + p);
  process.exit(1);
}
console.log('\nthe landing page renders');
process.exit(0);
