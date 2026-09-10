// Exercise the actual pointer/keyboard handlers without a browser or network.
const assert = require('node:assert/strict');
const {fitWidths, mountPanels} = require('../../site/panels.js');

function element() {
  const listeners = {}, classes = new Set(), attrs = {}, captured = new Set();
  return {
    attrs, dataset: {},
    classList: {add: k => classes.add(k), remove: k => classes.delete(k), contains: k => classes.has(k),
      toggle: (k, on) => on ? classes.add(k) : classes.delete(k)},
    addEventListener(name, fn) { (listeners[name] ||= []).push(fn); },
    emit(name, detail = {}) {
      const event = {button: 0, isPrimary: true, pointerId: 1, clientX: 400, preventDefault() {}, ...detail};
      (listeners[name] || []).forEach(fn => fn(event));
    },
    setAttribute(k, v) { attrs[k] = String(v); },
    setPointerCapture(id) { captured.add(id); },
    hasPointerCapture(id) { return captured.has(id); },
    releasePointerCapture(id) { captured.delete(id); },
    focus() {},
  };
}

function fixture(total = 1440, storage = new Map()) {
  const tasks = element(), board = element(), body = element(), media = element();
  const toggle = element(), filters = element();
  tasks.dataset.panelResizer = 'tasks'; board.dataset.panelResizer = 'board';
  const styles = {}, observers = [];
  media.matches = total >= 881;
  const root = {
    ...element(),
    ownerDocument: {body},
    querySelectorAll: () => [tasks, board],
    querySelector: selector => selector === '[data-task-toggle]' ? toggle : filters,
    getBoundingClientRect: () => ({width: total}),
    style: {setProperty: (k, v) => styles[k] = parseFloat(v)},
  };
  mountPanels(root, {
    matchMedia: () => media,
    localStorage: {
      getItem: k => storage.get(k) || null,
      setItem: (k, v) => storage.set(k, v),
      removeItem: k => storage.delete(k),
    },
    ResizeObserver: class { constructor(fn) { observers.push(fn); } observe() {} },
  });
  return {
    tasks, board, body, storage, toggle, filters, root,
    sizes: () => ({tasks: styles['--task-width'], board: styles['--board-width']}),
    resize(next) {
      total = next;
      if (next) {
        const changed = media.matches !== (next >= 881);
        media.matches = next >= 881;
        if (changed) media.emit('change');
      }
      observers.forEach(fn => fn());
    },
  };
}

function usable(total, sizes) {
  assert.ok(sizes.tasks >= 180 - 1e-6, 'task navigation must remain usable');
  assert.ok(sizes.board >= 280 - 1e-6, 'leaderboard must remain usable');
  assert.ok(total - 16 - sizes.tasks - sizes.board >= 320 - 1e-6, 'benchmark must remain usable');
}

for (const width of [881, 1024, 1440, 1920, 3000]) {
  for (const requested of [{tasks: 10, board: 10}, {tasks: 1000, board: 1000}, {tasks: 230, board: 500}]) {
    usable(width, fitWidths(width, requested));
  }
}
console.log('ok panel minimums across desktop widths');

const f = fixture(), initial = f.sizes();
f.tasks.emit('pointerdown');
f.tasks.emit('pointermove', {clientX: 500});
assert.equal(f.sizes().tasks, initial.tasks + 100);
assert.equal(f.sizes().board, initial.board, 'left drag leaves the right panel alone');
assert.ok(f.body.classList.contains('resizing-panels'));
f.tasks.emit('pointermove', {pointerId: 2, clientX: 1000});
assert.equal(f.sizes().tasks, initial.tasks + 100, 'ignore unrelated pointers');
f.tasks.emit('pointermove', {clientX: 10000});
usable(1440, f.sizes());
assert.equal(Math.round(1440 - 16 - f.sizes().tasks - f.sizes().board), 320);
f.tasks.emit('pointerup');
assert.ok(!f.body.classList.contains('resizing-panels'));
assert.deepEqual(fixture(1440, f.storage).sizes(), f.sizes(), 'reload restores widths');
console.log('ok drag boundaries, pointer capture, and saved widths');

f.board.emit('dblclick');
assert.deepEqual(f.sizes(), initial);
assert.equal(f.storage.size, 0);
f.board.emit('pointerdown');
f.board.emit('pointermove', {clientX: 350});
assert.equal(f.sizes().board, initial.board + 50, 'right divider grows its panel when dragged left');
assert.equal(f.sizes().tasks, initial.tasks);
f.board.emit('pointercancel');
assert.deepEqual(f.sizes(), initial, 'cancel rolls back a partial drag');
f.board.emit('pointerdown');
f.board.emit('pointermove', {clientX: 360});
f.board.emit('lostpointercapture');
assert.deepEqual(f.sizes(), initial);
console.log('ok right divider, reset, and cancelled drags');

f.tasks.emit('keydown', {key: 'ArrowRight'});
assert.equal(f.sizes().tasks, initial.tasks + 10);
f.board.emit('keydown', {key: 'ArrowLeft', shiftKey: true});
assert.equal(f.sizes().board, initial.board + 40);
f.tasks.emit('keydown', {key: 'Home'});
assert.equal(f.sizes().tasks, 180);
f.tasks.emit('keydown', {key: 'End'});
usable(1440, f.sizes());
assert.equal(f.tasks.attrs['aria-valuenow'], String(Math.round(f.sizes().tasks)));
assert.equal(f.tasks.attrs['aria-valuenow'], f.tasks.attrs['aria-valuemax']);
f.tasks.emit('keydown', {key: 'Enter'});
assert.deepEqual(f.sizes(), initial);
console.log('ok keyboard controls and accessible values');

f.tasks.emit('pointerdown');
f.tasks.emit('pointermove', {clientX: 10000});
f.tasks.emit('pointerup');
f.resize(881);
usable(881, f.sizes());
f.resize(0); // Switch to another page and back.
f.resize(881);
usable(881, f.sizes());
f.tasks.emit('pointerdown');
f.tasks.emit('pointermove', {clientX: 430});
f.resize(700);
assert.ok(!f.body.classList.contains('resizing-panels'));
const mobile = f.sizes();
f.tasks.emit('pointerdown');
f.tasks.emit('pointermove', {clientX: 1000});
f.tasks.emit('keydown', {key: 'ArrowRight'});
assert.deepEqual(f.sizes(), mobile, 'stacked mobile layout cannot be dragged');
f.resize(1200);
usable(1200, f.sizes());
assert.ok(Number(f.tasks.attrs['aria-valuemax']) >= Number(f.tasks.attrs['aria-valuenow']));
console.log('ok viewport changes, hidden tabs, and stacked mobile layout');

const folding = fixture();
folding.tasks.emit('keydown', {key: 'ArrowRight', shiftKey: true});
const expanded = folding.sizes();
folding.toggle.emit('click');
assert.equal(folding.sizes().tasks, 44);
assert.equal(folding.sizes().board, expanded.board, 'folding returns space to the benchmark');
assert.ok(folding.filters.hidden);
assert.ok(folding.root.classList.contains('tasks-collapsed'));
assert.equal(folding.toggle.attrs['aria-expanded'], 'false');
assert.equal(folding.toggle.attrs['aria-label'], 'Expand task filters');
assert.equal(folding.tasks.tabIndex, -1);
assert.equal(folding.tasks.attrs['aria-hidden'], 'true');
folding.tasks.emit('pointerdown');
folding.tasks.emit('pointermove', {clientX: 800});
folding.tasks.emit('keydown', {key: 'ArrowRight'});
assert.equal(folding.sizes().tasks, 44, 'collapsed divider cannot be dragged or keyboard-resized');
folding.toggle.emit('click');
assert.deepEqual(folding.sizes(), expanded, 'expanding restores the prior width');
assert.equal(folding.filters.hidden, false);
assert.equal(folding.tasks.tabIndex, 0);
assert.equal(folding.toggle.attrs['aria-expanded'], 'true');
folding.toggle.emit('click');
folding.board.emit('keydown', {key: 'ArrowLeft', shiftKey: true});
const restored = fixture(1440, folding.storage);
assert.equal(restored.sizes().tasks, 44, 'collapse state survives a reload');
assert.ok(restored.filters.hidden);
restored.toggle.emit('click');
assert.equal(restored.sizes().tasks, expanded.tasks, 'resizing while collapsed preserves expanded width');
assert.ok(Math.abs(restored.sizes().board - (expanded.board + 40)) < 1e-6, 'board width survives the save/restore round trip');
restored.toggle.emit('click');
restored.board.emit('keydown', {key: 'End'});
assert.equal(1440 - 8 - 44 - restored.sizes().board, 320, 'collapsed layout uses the freed width');
restored.resize(881);
assert.ok(881 - 8 - 44 - restored.sizes().board >= 320 - 1e-6);
restored.toggle.emit('click');
usable(881, restored.sizes());
console.log('ok collapse, accessible toggle, expanded-width restoration, and saved state');

const mobileFilters = fixture(700);
mobileFilters.toggle.emit('click');
assert.ok(mobileFilters.filters.hidden, 'mobile filters can collapse before desktop layout initializes');
mobileFilters.resize(1440);
assert.equal(mobileFilters.sizes().tasks, 44);
mobileFilters.toggle.emit('click');
usable(1440, mobileFilters.sizes());
mobileFilters.tasks.emit('pointerdown');
mobileFilters.tasks.emit('pointermove', {clientX: 550});
mobileFilters.toggle.emit('click');
assert.ok(!mobileFilters.body.classList.contains('resizing-panels'), 'collapse cancels an active drag');
mobileFilters.resize(700);
mobileFilters.toggle.emit('click');
assert.equal(mobileFilters.filters.hidden, false);
console.log('ok mobile disclosure and cancelling a drag when folding');

const corrupt = new Map([['ssa.panel-widths.v1', '{broken']]);
usable(1440, fixture(1440, corrupt).sizes());
const blocked = {get() { throw new Error('storage blocked'); }, set() { throw new Error('storage blocked'); }, delete() { throw new Error('storage blocked'); }};
const noStorage = fixture(1440, blocked);
noStorage.tasks.emit('keydown', {key: 'ArrowRight'});
usable(1440, noStorage.sizes());
noStorage.toggle.emit('click');
assert.equal(noStorage.sizes().tasks, 44);
noStorage.toggle.emit('click');
usable(1440, noStorage.sizes());
console.log('ok corrupted or unavailable storage');

// Draw every chart shape with the real published data and the actual inline
// code. Widths refer to the SVG viewport, independent of the window width.
const fs = require('fs'), path = require('path');
const {installGlobals, node, leaks} = require('./dom_stub.js');
const html = fs.readFileSync(path.join(__dirname, '../../site/index.html'), 'utf8');
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
const {els, window: testWindow} = installGlobals();
const svg = node('svg'), tip = node('div'), attrs = {};
svg.setAttribute = (k, v) => attrs[k] = v;
const pane = document.getElementById('chartpane');
pane.querySelector = selector => selector === 'svg' ? svg : tip;
const charts = new Function(script + '\nreturn {render, renderTaskChart, TASKS};')();
charts.render(JSON.parse(fs.readFileSync(path.join(__dirname, '../../site/data.json'), 'utf8')));
const drawnKinds = new Set();
for (const width of [288, 320, 450, 800, 1400]) {
  svg.clientWidth = width; svg.clientHeight = 360;
  for (const [key, task] of Object.entries(charts.TASKS)) {
    testWindow.__tab = key;
    // Cover model backtests and raw tracker history where both are available.
    for (const unit of ['arena', 'level']) {
      testWindow.__unitSer = unit;
      charts.renderTaskChart(key);
      assert.doesNotMatch(svg.innerHTML, /NaN|undefined|Invalid Date/, key + ' chart geometry');
      if (svg.style.display !== 'none' && attrs.viewBox) {
        assert.equal(Number(attrs.viewBox.split(' ')[2]), width, key + ' uses its actual panel width');
        if(svg.innerHTML) drawnKinds.add(key === 'agg' ? 'aggregate' : task.kind || 'scalar');
      }
    }
  }
  assert.deepEqual(leaks(els), []);
}
assert.deepEqual([...drawnKinds].sort(), ['aggregate', 'profile', 'ranking', 'scalar']);
console.log('ok all chart shapes at five panel widths');
console.log('resizable panels pass');
