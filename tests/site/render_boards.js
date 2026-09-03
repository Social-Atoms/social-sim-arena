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

// The weekly calendar. One row per real batch and not one more: grouping by
// `deadline` instead of `batch_id` invented a batch per pre-cutover round --
// six single-question weeks that no participant is ever handed.
const batchIds = new Set((data.rounds || []).map(r => r.batch_id).filter(Boolean));
const batchBody = els['batch-body'].innerHTML;
const batchRows = batchBody.split('</tr>').filter(r => r.trim()).length;
if (batchIds.size) {
  if (batchRows > batchIds.size) {
    problems.push(`the calendar shows ${batchRows} batches; the payload names `
      + `${batchIds.size}. Grouping is inventing batches.`);
  }
  for (const m of batchBody.matchAll(/batch-(\d{4}-\d{2}-\d{2})/g)) {
    if (!batchIds.has('batch-' + m[1])) {
      problems.push(`the calendar shows batch-${m[1]}, which no round claims`);
    }
  }
  // A round with no batch_id predates the weekly calendar and belongs on no
  // week; if any of their ids reach the table, the null is being ignored.
  const unbatched = (data.rounds || []).filter(r => !r.batch_id);
  for (const r of unbatched.slice(0, 40)) {
    if (batchBody.includes(r.round_id)) {
      problems.push(`${r.round_id} has no batch_id and is on the calendar`);
    }
  }
} else if (!/predates the weekly calendar/.test(batchBody)) {
  problems.push('no round carries a batch_id and the calendar does not say so');
}
console.log(`weekly calendar: ${batchIds.size} batches, ${batchRows} rows`);

// Source freshness: published since the reliability work landed, rendered
// nowhere until now. A row per source, and a late source must not read "ok".
const healthRows = (els['health-body'].innerHTML.match(/<tr>/g) || []).length;
const expectHealth = (data.source_health || []).length;
if (expectHealth && healthRows !== expectHealth) {
  problems.push(`source freshness: ${expectHealth} sources published, `
    + `${healthRows} rows rendered`);
}
if (expectHealth && !/last fetched|Last fetched/i.test(html)) {
  problems.push('the freshness table has no column saying when a source was '
    + 'last fetched');
}
for (const h of data.source_health || []) {
  const over = (typeof h.changed_days === 'number'
    && typeof h.budget_change_days === 'number'
    && h.changed_days > h.budget_change_days);
  if (over) {
    const row = els['health-body'].innerHTML.split('<tr>')
      .find(c => c.includes('>' + h.source + '<')) || '';
    if (/>ok</.test(row)) {
      problems.push(`${h.source} has not moved in ${h.changed_days}d against a `
        + `${h.budget_change_days}d budget and the table says "ok"`);
    }
  }
}

// Per-round results. A resolved round carries the outcome and every
// entrant's answer, and for a long time no page rendered either -- the board
// showed an aggregate and asked to be taken on trust.
const roundHtml = (els['rounds-body'].children || []).map(c => c.innerHTML);
const resolved = (data.rounds || []).filter(
  r => r.resolution && typeof r.resolution.value === 'number');
const withResult = roundHtml.filter(h => h.includes('class="result"')).length;
if (resolved.length && withResult !== resolved.length) {
  problems.push(`${resolved.length} rounds published an outcome, `
    + `${withResult} show it`);
}
for (const r of resolved) {
  const row = roundHtml.find(h => h.includes(r.round_id));
  if (!row) continue;
  const n = Object.values(r.forecasts || {})
    .filter(f => f && typeof f.mean === 'number').length;
  if (n && !/<tbody>/.test(row)) {
    problems.push(`${r.round_id} has ${n} scalar forecasts and renders no table`);
  }
  // A corrected resolution must stay visible. One round here was first
  // resolved against a number that was public before its own lock; hiding
  // that the correction happened would be the wrong way to look clean.
  if (r.resolution.corrected && !/corrected/.test(row)) {
    problems.push(`${r.round_id} was corrected and the page does not say so`);
  }
}
console.log(`per-round results: ${withResult}/${resolved.length} resolved rounds`);

const roundRows = (els['rounds-body'].children || []).length;
if (!roundRows) problems.push('rounds table rendered no rows');
const meta = els['meta-line'].innerHTML;
if (!/updated \S/.test(meta)) problems.push(`meta line has no update time: "${meta}"`);

for (const r of out) {
  console.log(`${r.label.padEnd(20)} col5=${r.head.padEnd(7)} rows=${String(r.rows).padEnd(4)}`
    + (r.empty ? ` EMPTY -> ${r.text}` : ` note="${r.note}…"`));
}
console.log(`source freshness: ${healthRows} rows`);
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
