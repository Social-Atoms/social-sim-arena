// Runs site/index.html's own script against a real data.json. The landing page
// is the first thing a participant sees; this asserts it renders, that the
// open-round list names each round's answer shape, and that every date it puts
// in front of a participant is the deadline they are held to rather than the
// arena's later internal lock.
const fs = require('fs');
const path = require('path');
const {installGlobals, leaks} = require('./dom_stub.js');

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
  // One question card per round; a shaped round wears the marked shape tag
  // ("profile · 16 cells", "ranking") in its header.
  const row = list.split('<div class="qcard')
    .find(chunk => chunk.includes(r.round_id)) || '';
  if (!/class="qshape special"/.test(row)) {
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

console.log(`question cards  : ${(list.match(/class="qcard/g) || []).length}`);
console.log(`midterm rows    : ${(exam.match(/class="vrow"/g) || []).length}`);
console.log(`shaped rounds   : ${shaped.length} in data, ${shownShaped.length} on the page, `
  + `${(list.match(/class="qshape special"/g) || []).length} shape tags rendered`);
console.log(`footer          : ${els['foot-updated'].textContent}`);

// The three-week calendar has to count every answer that lands inside it,
// including answers to questions that already closed. Built from the open
// rounds alone, it showed nothing arriving for anything past its close --
// measured on the committed payload, 31 of the 54 answers landing in the
// visible weeks were missing, and by mid-week that is most of them.
{
  const weekly = els['weekly-content'].innerHTML || '';
  const from = (() => { const x = new Date(); x.setUTCHours(0, 0, 0, 0);
    x.setUTCDate(x.getUTCDate() - ((x.getUTCDay() + 6) % 7)); return x.getTime(); })();
  const to = from + 21 * 86400000;
  const inGrid = iso => { const t = Date.parse(iso); return t >= from && t < to; };
  const landing = data.rounds.filter(r => r.release_at && inGrid(r.release_at));
  const tagged = [...weekly.matchAll(/<div class="wc-tag res"><b>(\d+)<\/b>/g)]
    .reduce((n, m) => n + Number(m[1]), 0);
  if (landing.length && tagged !== landing.length) {
    problems.push(`the calendar counts ${tagged} answers landing in the three `
      + `weeks it shows; ${landing.length} land there`);
  }
  const closing = data.rounds.filter(r =>
    r.status === 'open' && Date.parse(r.deadline || r.lock_at) > Date.now()
    && inGrid(r.deadline || r.lock_at));
  const closeTagged = [...weekly.matchAll(/<div class="wc-tag close"><b>(\d+)<\/b>/g)]
    .reduce((n, m) => n + Number(m[1]), 0);
  if (closing.length && closeTagged !== closing.length) {
    problems.push(`the calendar counts ${closeTagged} closes; ${closing.length} `
      + 'questions close in the weeks it shows');
  }
  console.log(`calendar        : ${closeTagged} closes, ${tagged} answers land`);
}

// A rendered `undefined` or `NaN` is a bug the reader sees before anyone else
// does, and it survives every check that only counts rows.
for (const leak of leaks(els)) {
  problems.push(`rendered "${leak.what}": …${leak.near}…`);
}

if (problems.length) {
  console.error('\n' + problems.length + ' problem(s):');
  for (const p of problems) console.error('  - ' + p);
  process.exit(1);
}
console.log('\nthe landing page renders');
process.exit(0);
