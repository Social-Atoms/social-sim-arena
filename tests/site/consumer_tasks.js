const fs = require('fs');
const path = require('path');
const {installGlobals} = require('./dom_stub.js');
const root = path.resolve(__dirname, '../..');
const {els} = installGlobals();
const html = fs.readFileSync(path.join(root, 'site/index.html'), 'utf8');
const script = (html.match(/<script>([\s\S]*?)<\/script>/g) || [])
  .map(s => s.replace(/^<script>/, '').replace(/<\/script>$/, '')).join('\n');
eval(script);
const data = JSON.parse(fs.readFileSync(path.join(root, 'site/data.json')));
render(data);
for (const id of ['consumer_sce','consumer_michigan','consumer_mint','consumer_nyt',
                  'consumer_steam','consumer_netflix','consumer_acsi']) {
  renderTaskHeader(data, id);
  window.__tab=id;
  renderBoard('all');
  if ((els['lb-body'].innerHTML||'').includes('Claude') || (els['lb-body'].innerHTML||'').includes('EWMA'))
    throw Error(`${id}: unrelated source scores leaked onto a new task`);
  if (!els['task-title'].textContent) throw Error(`${id}: missing title`);
  if (['consumer_netflix','consumer_acsi'].includes(id)) {
    if (!els['task-question-summary'].textContent.includes('awaiting permission'))
      throw Error(`${id}: permission state hidden`);
    if (!els['task-question-body'].innerHTML.includes('No forecasts'))
      throw Error(`${id}: inactive contract missing`);
  }
}
for (const round of data.rounds.filter(r => r.consumer)) {
  renderQuestion(round.round_id);
  const body = els['question-col'].innerHTML;
  if (!body.includes(round.question) || !body.includes(round.attribution.replace(/&/g,'&amp;')))
    throw Error(`${round.round_id}: question or required source attribution missing`);
}
console.log('Seven consumer task registrations and four live question pages rendered');
