// Runs site/index.html's own script against a data.json the pipeline produced
// and renders the question page and the single-forecast page for each answer
// shape. Exits non-zero with the reason when a scored profile or ranking
// question wears the number template (Error / CRPS columns, "no number
// forecasts"), when the two pages disagree about a forecast's score, or when
// the payload lacks one of the three samples, so that a thinner payload cannot
// pass by rendering less.
//
// The payload must already have been through `refresh.attach_round_scores`
// (tests/test_site_render.py does that with the real function); nothing is
// patched here.
const fs = require('fs');
const path = require('path');
const {installGlobals} = require('./dom_stub.js');

const root = path.resolve(__dirname, '..', '..');
const dataPath = process.argv[2];
if (!dataPath) {
  console.error('usage: node render_question.js <data.json produced by the pipeline>');
  process.exit(2);
}
const html = fs.readFileSync(path.join(root, 'site', 'index.html'), 'utf8');
const script = (html.match(/<script>([\s\S]*?)<\/script>/g) || [])
  .map(b => b.replace(/^<script>/, '').replace(/<\/script>$/, '')).join('\n');

const {els} = installGlobals();
const data = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
const problems = [];

eval(script);
render(data);

const byId = Object.fromEntries((data.rounds || []).map(r => [r.round_id, r]));
const scoredProfile = ((data.profile || {}).rounds || [])[0];
const scoredRanking = ((data.ranking || {}).rounds || [])[0];
const numberRound = (data.rounds || []).find(r =>
  (r.target_type || 'continuous_normal') === 'continuous_normal' && r.status === 'resolved'
  && r.scores && r.scores.crowd && typeof r.scores.crowd.crps === 'number');

if (!scoredRanking) problems.push('no scored ranking round in the payload; the ranking template is untested');
if (!scoredProfile) problems.push('no scored profile round in the payload; the profile template is untested');
if (!numberRound) problems.push('no resolved number round with a scored crowd forecast; the crowd score is untested');

function questionPage(id) {
  renderQuestion(id);
  return els['question-col'].innerHTML || '';
}
function forecastPage(id, entrant) {
  renderForecast(id, entrant);
  return els['forecast-col'].innerHTML || '';
}

if (scoredRanking) {
  const r = byId[scoredRanking.round_id];
  if (!r || r.status !== 'resolved') problems.push(`${scoredRanking.round_id}: scored on the ranking board but its round is not resolved; attach_round_scores did not run on this payload`);
  const q = questionPage(scoredRanking.round_id);
  if (!/<th class="num">Loss<\/th>/.test(q) || /<th class="num">CRPS<\/th>/.test(q)) {
    problems.push(`${scoredRanking.round_id}: a scored ranking question shows the number template's columns`);
  }
  if (!q.includes('The released list')) problems.push(`${scoredRanking.round_id}: the released list is not shown`);
  if (!/class="qdue done">resolved/.test(q)) problems.push(`${scoredRanking.round_id}: a scored question is not shown as resolved`);
  if (q.includes('no number forecasts')) problems.push(`${scoredRanking.round_id}: says "no number forecasts" about a ranking`);
  const first = (scoredRanking.entries || [])[0];
  if (!first) problems.push(`${scoredRanking.round_id}: no scored entries to render`);
  else {
    const f = forecastPage(scoredRanking.round_id, first.entrant);
    if (!f.includes(first.loss.toFixed(3))) problems.push(`${scoredRanking.round_id}/${first.entrant}: the forecast page does not show its loss ${first.loss}`);
    if (!f.includes('The released list')) problems.push(`${scoredRanking.round_id}/${first.entrant}: the forecast page hides the released list`);
    if (/CRPS/.test(f)) problems.push(`${scoredRanking.round_id}/${first.entrant}: a ranking forecast page mentions CRPS`);
  }
  console.log(`ranking question : ${scoredRanking.round_id}, ${(scoredRanking.entries || []).length} scored`);
}

if (scoredProfile) {
  const r = byId[scoredProfile.round_id];
  if (!r || r.status !== 'resolved') problems.push(`${scoredProfile.round_id}: scored on the profile board but its round is not resolved; attach_round_scores did not run on this payload`);
  const q = questionPage(scoredProfile.round_id);
  if (!/<th class="num">Energy<\/th>/.test(q) || /<th class="num">CRPS<\/th>/.test(q)) {
    problems.push(`${scoredProfile.round_id}: a scored profile question shows the number template's columns`);
  }
  if (!q.includes('The released profile')) problems.push(`${scoredProfile.round_id}: the released profile is not shown`);
  if (!/class="qdue done">resolved/.test(q)) problems.push(`${scoredProfile.round_id}: a scored question is not shown as resolved`);
  const first = (scoredProfile.entries || [])[0];
  if (!first) problems.push(`${scoredProfile.round_id}: no scored entries to render`);
  else {
    const f = forecastPage(scoredProfile.round_id, first.entrant);
    if (!f.includes(first.energy.toFixed(3))) problems.push(`${scoredProfile.round_id}/${first.entrant}: the forecast page does not show its energy ${first.energy}`);
    if (!f.includes('The released profile')) problems.push(`${scoredProfile.round_id}/${first.entrant}: the forecast page hides the released profile`);
  }
  console.log(`profile question : ${scoredProfile.round_id}, ${(scoredProfile.entries || []).length} scored`);
}

if (numberRound) {
  const want = numberRound.scores.crowd.crps.toFixed(2);
  const q = questionPage(numberRound.round_id);
  const f = forecastPage(numberRound.round_id, 'crowd');
  if (!/<th class="num">CRPS<\/th>/.test(q)) problems.push(`${numberRound.round_id}: a number question lost its CRPS column`);
  // The crowd forecast is a quantile mixture; both pages must show the
  // pipeline's score, never a normal refitted to the mean and sd.
  const crowdRow = (q.split('<tr class="link"').find(x => x.includes('data-e="crowd"')) || '');
  if (!crowdRow.includes('>' + want + '<')) problems.push(`${numberRound.round_id}: the question page shows the crowd at something other than CRPS ${want}`);
  if (!f.includes('CRPS ' + want)) problems.push(`${numberRound.round_id}/crowd: the forecast page shows something other than CRPS ${want}`);
  console.log(`number question  : ${numberRound.round_id}, crowd CRPS ${want} on both pages`);

  // The entrant page's per-task CRPS is the mean of the same published scores.
  const label = taskForRound(numberRound).label;
  const taskRounds = (data.rounds || []).filter(r => r.forecasts && r.forecasts.crowd
    && taskForRound(r).label === label && typeof (r.resolution || {}).value === 'number');
  const published = taskRounds.map(r => ((r.scores || {}).crowd || {}).crps).filter(v => typeof v === 'number');
  if (published.length === taskRounds.length && published.length) {
    const mean = (published.reduce((a, b) => a + b, 0) / published.length).toFixed(2);
    renderEntrant('crowd');
    const page = els['entrant-col'].innerHTML || '';
    const row = (page.split('<tr>').find(x => x.startsWith('<td class="q">' + label + '</td>')) || '');
    const cells = [...row.matchAll(/<td class="num">([^<]*)<\/td>/g)].map(m => m[1]);
    if (cells[cells.length - 1] !== mean) {
      problems.push(`#entrant/crowd: the ${label} row shows CRPS ${cells[cells.length - 1] || '(none)'}, the mean of the published scores is ${mean}`);
    }
    console.log(`entrant page     : crowd on ${label}, mean CRPS ${mean} over ${published.length} question(s)`);
  } else {
    console.log(`entrant page     : crowd on ${label} not checked, ${published.length} of ${taskRounds.length} resolved questions carry a published crowd score`);
  }
}

if (problems.length) {
  console.error('\n' + problems.join('\n'));
  process.exit(1);
}
console.log('\nquestion and forecast pages render each shape with its own score');
