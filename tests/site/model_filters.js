// Exercise the shipped model controls against published data, without a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {installGlobals, node, leaks} = require('./dom_stub.js');
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'site/index.html'), 'utf8');
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]).join('\n');
const data = JSON.parse(fs.readFileSync(path.join(root, 'site/data.json'), 'utf8'));
const storageKey = 'ssa.chart-models.v1';
// The picker's mechanics are checked against the backtest roster, which the site falls back to when no
// question has resolved; the season's own default is checked at the end against the real payload.
const backtestOnly = JSON.parse(JSON.stringify(data)); backtestOnly.rounds.forEach(r=>{ delete r.scores; });

function fixture(storage = new Map(), payload = backtestOnly) {
  const {els, window: win, document: doc} = installGlobals();
  const svg = node('svg'), tip = node(), events = {}, stripEvents = {};
  svg.clientWidth = 600; svg.clientHeight = 360;
  doc.getElementById('chartpane').querySelector = s=>s==='svg'?svg:tip;
  win.localStorage = {getItem:k=>storage.get(k)??null, setItem:(k,v)=>storage.set(k,v)};
  doc.addEventListener = (name, fn)=>{events[name]=fn;};
  doc.getElementById('chart-filters').addEventListener = (name, fn)=>{stripEvents[name]=fn;};
  for(const id of ['model-trigger', 'model-search']) {
    doc.getElementById(id).focus = ()=>{doc.activeElement=els[id];};
  }
  // Keep checkbox nodes stable until the actual menu markup changes, as a DOM
  // does; a selection redraw must update checked state without replacing them.
  let markup, inputs = [];
  const options = doc.getElementById('model-options');
  options.querySelectorAll = ()=>{
    if(markup !== options.innerHTML) {
      markup = options.innerHTML;
      inputs = [...markup.matchAll(/<input type="checkbox" data-model="([^"]+)"( checked)?>/g)]
        .map(m=>({dataset:{model:m[1]}, checked:!!m[2], matches:s=>s==='input[data-model]'}));
    }
    return inputs;
  };
  const api = new Function(script+'\nreturn {render, renderTaskChart, renderLegend, matchingModels, coerceUnknown, modelInfo, selectModels, resetModels, setPickerFilter, modelPicker, TASKS};')();
  api.render(payload);
  api.renderTaskChart('agg');
  return {api, els, win, doc, svg, events, stripEvents, storage, options};
}

let f = fixture();
const selected = f.api.modelPicker.ids.filter(id=>!f.win.__hidden.has(id));
assert.equal(selected.length, 7, 'default comparison is six entrants and EWMA');
assert.ok(selected.includes('ewma'));
assert.equal(f.els['model-selected-count'].textContent, '7 of '+f.api.modelPicker.ids.length+' entrants');
const originalBoard = f.els['lb-body'].innerHTML;
const originalSelection = [...f.win.__keep];
f.els['model-trigger'].onclick();
assert.equal(f.els['model-pop'].hidden, false);
assert.equal(f.doc.activeElement, f.els['model-search']);
assert.match(f.options.innerHTML, /<legend>Baselines/);
// The backtest roster is entirely retired by now; the status dropdown opens the whole list.
f.api.setPickerFilter('status','all');
assert.match(f.els['model-filters'].innerHTML, /Everyone/);
assert.match(f.options.innerHTML, /recent-10/);
assert.match(f.options.innerHTML, /zero-shot/);
assert.match(f.options.innerHTML, /model-retired/);

f.els['model-search'].oninput({target:{value:'  OPENAI  '}});
assert.equal(f.els['model-result-count'].textContent, '6 results');
f.api.setPickerFilter('setup','zs');
assert.equal(f.els['model-result-count'].textContent, '3 results');
assert.deepEqual([...f.win.__keep], originalSelection, 'searching does not alter selection');
const matching = f.options.querySelectorAll();
assert.equal(matching.length, 3);
assert.ok(matching.every(input=>input.dataset.model.endsWith('-zeroshot')));
f.els['model-select-results'].onclick();
assert.ok(matching.every(input=>input.checked), 'batch select updates existing checkboxes');
assert.ok(matching.every(input=>f.win.__keep.has(input.dataset.model)));
assert.ok(originalSelection.every(id=>f.win.__keep.has(id)), 'bulk selection preserves other labs and setups');
assert.equal(f.doc.activeElement, f.els['model-search'], 'chart redraw preserves input focus');
assert.equal(f.els['model-pop'].hidden, false);
const selectedVariant = matching[0];
selectedVariant.checked = false;
f.options.onchange({target:selectedVariant});
assert.ok(f.win.__hidden.has(selectedVariant.dataset.model));
assert.ok(f.svg.innerHTML.length > 0);
f.els['model-clear-results'].onclick();
assert.deepEqual([...f.win.__keep], originalSelection, 'clear results only clears matching variants');
assert.ok(matching.every(input=>!input.checked));
assert.equal(f.els['lb-body'].innerHTML, originalBoard, 'chart selection does not change leaderboard');

f.els['model-search'].oninput({target:{value:'no-such-model'}});
assert.match(f.options.innerHTML, /No entrants match/);
assert.equal(f.els['model-select-results'].disabled, true);
assert.equal(f.els['model-clear-results'].disabled, true);
f.stripEvents.keydown({key:'Escape', preventDefault(){}});
assert.equal(f.els['model-pop'].hidden, true);
assert.equal(f.doc.activeElement, f.els['model-trigger']);
f.els['model-trigger'].onclick();
f.events.pointerdown({target:node()});
assert.equal(f.els['model-pop'].hidden, true);
f.els['model-trigger'].onclick();
f.els['model-done'].onclick();
assert.equal(f.doc.activeElement, f.els['model-trigger']);
console.log('ok search, labs, setup variants, scoped bulk actions, and keyboard dismissal');

// Each curve follows the selection, including the all-off case. Switching
// tasks or hiding the chooser on a profile never silently resets it.
const ids = [...f.api.modelPicker.ids];
f.api.selectModels(ids, false);
assert.match(f.svg.innerHTML, /Choose entrants above to compare/);
assert.match(f.els['model-chips'].innerHTML, /Choose entrants to start comparing/);
f.api.selectModels(['gpt-5.6-luna-zeroshot'], true);
assert.match(f.svg.innerHTML, /<title>GPT-5.6 Luna \(zero-shot\)<\/title>/);
const curve = f.svg.innerHTML.match(/<g clip-path="url\(#clip-agg\)"><path d="([^"]+)"/)[1];
const points = [...curve.matchAll(/[ML]([\d.]+) ([\d.-]+)/g)];
assert.ok(points.length > 1);
assert.ok(points.every(p=>Number(p[2])>=18 && Number(p[2])<=334),
  'the selected model stays inside the chart even when its score is below -30');
f.api.renderLegend([], {});
assert.equal(f.els['legend-strip'].style.display, '', 'the picker stays in place with nothing to draw');
assert.match(f.els['model-chips'].innerHTML, /No published forecasts to draw/);
assert.equal(f.els['model-selected-count'].textContent, 'no entrants');
const scalar = Object.keys(f.api.TASKS).find(k=>k!=='agg' && f.api.TASKS[k].per);
f.win.__tab = scalar;
f.api.renderTaskChart(scalar);
f.win.__tab = 'agg';
f.api.renderTaskChart('agg');
assert.ok(!f.win.__hidden.has('gpt-5.6-luna-zeroshot'));
assert.ok(f.win.__hidden.has('ewma'));
const saved = f.storage;
f = fixture(saved);
assert.equal(f.els['model-selected-count'].textContent, '1 of 29 entrants');
assert.ok(!f.win.__hidden.has('gpt-5.6-luna-zeroshot'));
f.api.coerceUnknown(['future-entrant']);
assert.ok(f.win.__hidden.has('future-entrant'), 'new models do not flood a saved comparison');
f.api.selectModels(['future-entrant'], true);
f = fixture(saved);
f.api.coerceUnknown(['future-entrant']);
assert.ok(!f.win.__hidden.has('future-entrant'), 'explicit choices for new entrants persist');
f.els['model-reset'].onclick();
assert.equal(f.els['model-selected-count'].textContent, '7 of '+f.api.modelPicker.ids.length+' entrants');
assert.deepEqual(leaks(f.els), []);
console.log('ok chart updates, empty state, task switching, reset, and selection persistence');

for(const value of ['{broken', '{}', '[123]']) {
  f = fixture(new Map([[storageKey,value]]));
  assert.equal(f.els['model-selected-count'].textContent, '7 of '+f.api.modelPicker.ids.length+' entrants');
}
f = fixture({get(){throw Error('denied');}, set(){throw Error('denied');}});
f.api.selectModels(['ewma'],false);
assert.ok(f.win.__hidden.has('ewma'));
f.api.resetModels();
assert.ok(!f.win.__hidden.has('ewma'));
// With the season's scores present, the default comparison is the season's top six and EWMA, never the crowd.
{ const live = fixture(new Map(), data); const sel = live.api.modelPicker.ids.filter(id=>!live.win.__hidden.has(id)); assert.equal(sel.length, 7, 'the season picks its own default'); assert.ok(sel.includes('ewma')); assert.ok(!sel.includes('crowd')); }
console.log('ok malformed and unavailable storage');
