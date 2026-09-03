// Runs site/leaderboard.html's own script against a real data.json and reports
// what a participant would see on every tab. Exits non-zero with the reason
// when a tab renders nothing, loses its column label, or renders an empty
// state that names no round.
const fs = require('fs');
const path = require('path');
const {installGlobals} = require('./dom_stub.js');

const root = path.resolve(__dirname, '..', '..');
const dataPath = process.argv[2] || path.join(root, 'site', 'data.json');
const html = fs.readFileSync(path.join(root, 'site', 'leaderboard.html'), 'utf8');
const script = (html.match(/<script>([\s\S]*?)<\/script>/g) || [])
  .map(b => b.replace(/^<script>/, '').replace(/<\/script>$/, '')).join('\n');

const {els} = installGlobals();

const data = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
const problems = [];
// Direct eval in this module's (non-strict) scope, so the page's own function
// declarations are visible below. Under `'use strict'` they would evaluate
// into a scope that disappears on the next line, which is why this file does
// not declare it.
eval(script);
render(data);

const TABS = [
  ['tab-bt', 'CRPS', 'Backtest'],
  ['tab-live', 'CRPS', 'Season 0'],
  ['tab-profile', 'Energy', 'Population profile'],
  ['tab-ranking', 'Loss', 'Ranking'],
];

const out = [];
for (const [id, wantHead, label] of TABS) {
  const tab = els[id];
  if (!tab || typeof tab.onclick !== 'function') {
    problems.push(`${label}: tab ${id} has no click handler`);
    continue;
  }
  tab.onclick();
  const body = els['lb-body'].innerHTML;
  const head = els['lb-loss-head'].textContent;
  const note = els['lb-note'].textContent;
  const rows = (body.match(/<tr>/g) || []).length;
  const text = body.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();

  if (rows === 0) problems.push(`${label}: rendered no rows at all`);
  if (head !== wantHead) {
    problems.push(`${label}: third-number column says "${head}", expected "${wantHead}"`);
  }
  if (!note) problems.push(`${label}: no explanation of what the board measures`);
  // An empty board must say which round fills it. "pending" alone reads as
  // broken to the participant who was asked to answer that shape.
  const empty = /has resolved yet/.test(text);
  if (empty && !/resolves with \S/.test(text)) {
    problems.push(`${label}: empty state names no round -- "${text.slice(0, 90)}"`);
  }
  out.push({label, head, rows, empty, text: text.slice(0, 100), note: note.slice(0, 60)});
}

const roundRows = (els['rounds-body'].children || []).length;
if (!roundRows) problems.push('rounds table rendered no rows');
const meta = els['meta-line'].innerHTML;
if (!/updated \S/.test(meta)) problems.push(`meta line has no update time: "${meta}"`);

for (const r of out) {
  console.log(`${r.label.padEnd(20)} col5=${r.head.padEnd(7)} rows=${String(r.rows).padEnd(4)}`
    + (r.empty ? ` EMPTY -> ${r.text}` : ` note="${r.note}…"`));
}
console.log(`rounds table: ${roundRows} rows`);
console.log(`meta: ${meta.replace(/<[^>]*>/g, '')}`);

if (problems.length) {
  console.error('\n' + problems.length + ' problem(s):');
  for (const p of problems) console.error('  - ' + p);
  process.exit(1);
}
console.log('\nevery board renders');
// The page ends by kicking off fetches; nothing here waits on them.
process.exit(0);
