// Run the real inline navigation script, not a reimplementation of its algorithm.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('site/docs.html', 'utf8');
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map(m => m[1]).find(s => s.includes('const sectionLinks='));
const sidebar = html.match(/<aside class="left"[\s\S]*?<\/aside>/)[0];
const ids = [...sidebar.matchAll(/href="#([^"]+)"/g)].map(m => m[1]);

function setup({origin = 51, hash = '', root = false} = {}) {
  let frames = [], clock = 0;
  function eventTarget(extra = {}) {
    const listeners = {};
    return Object.assign({
      addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
      emit(type, event = {}) { for (const fn of listeners[type] || []) fn(event); },
    }, extra);
  }
  const scroller = eventTarget({
    scrollTop: 0, clientTop: 0, clientHeight: 700,
    scrollHeight: ids.length * 600,
    getBoundingClientRect: () => ({top: root ? 0 : origin}),
  });
  const headings = Object.fromEntries(ids.map((id, i) => [id, {
    id, y: 100 + i * 600, margin: 74,
    getBoundingClientRect() { return {top: (root ? 0 : origin) + this.y - scroller.scrollTop}; },
    scrollIntoView() { scroller.scrollTop = Math.max(0, Math.min(
      scroller.scrollHeight - scroller.clientHeight, this.y - this.margin)); },
  }]));
  const links = ids.map(id => eventTarget({
    hash: '#' + id, textContent: id, on: false,
    classList: {toggle(_, on) { links.find(l => l.hash === '#' + id).on = on; }},
  }));
  const rightLink = eventTarget({hash: '#retirement'});
  const location = {hash, replace(to) { this.hash = to; }};
  const document = eventTarget({
    title: '', scrollingElement: root ? scroller : {},
    querySelector(selector) { return selector === '.page-scroll' && !root ? scroller : null; },
    querySelectorAll(selector) {
      if (selector === '.left a[href^="#"]') return links;
      if (selector.startsWith('.layout a')) return [...links, rightLink];
      return [];
    },
    getElementById: id => headings[id] || null,
  });
  const window = eventTarget({matchMedia: () => ({matches: false})});
  vm.runInNewContext(script, {
    document, window, location, matchMedia: window.matchMedia,
    getComputedStyle: el => ({scrollMarginTop: el.margin + 'px'}),
    requestAnimationFrame: fn => frames.push(fn),
    Date: {now: () => clock}, setTimeout() {}, navigator: {},
  });
  const flush = () => { const batch = frames; frames = []; batch.forEach(fn => fn()); };
  const scroll = top => { scroller.scrollTop = top; scroller.emit('scroll'); flush(); };
  const active = () => links.filter(l => l.on).map(l => l.hash);
  const expect = id => assert.deepEqual(active(), ['#' + id]);
  const click = (id, link = links.find(l => l.hash === '#' + id)) => {
    link.emit('click', {button: 0}); location.hash = '#' + id; window.emit('hashchange');
  };
  const arrive = id => { headings[id].scrollIntoView(); scroll(scroller.scrollTop); };
  return {headings, scroller, window, document, location, rightLink, flush, scroll,
    expect, click, arrive, later: () => { clock += 2000; }};
}

// Regression: native anchors land at 51 + 74 = 125 px, beyond the old 90 px cutoff.
for (const options of [{origin: 51}, {origin: 104}, {root: true}]) {
  const t = setup(options);
  for (const id of ids) {
    t.click(id); t.arrive(id); t.later(); t.scroll(t.scroller.scrollTop);
    t.expect(id);
  }
}
console.log('ok all sidebar destinations, mobile header, and root-scroll fallback');

const slow = setup();
slow.click('retirement'); slow.later();
slow.scroll(slow.headings.sealbox.y); slow.expect('retirement');
slow.arrive('retirement'); slow.scroll(slow.scroller.scrollTop); slow.expect('retirement');
const title = slow.document.title;
slow.scroller.emit('wheel'); slow.scroll(slow.headings.sealbox.y); slow.expect('sealbox');
assert.equal(slow.document.title, title, 'reading must not rename the navigated tab');
console.log('ok long smooth scroll, settled highlight, and manual reading');

for (const type of ['wheel', 'touchstart', 'pointerdown', 'keydown']) {
  const t = setup(); t.click('retirement');
  if (type === 'keydown') t.document.emit(type, {key: 'PageUp'});
  else t.scroller.emit(type);
  t.scroll(t.headings.integrity.y); t.expect('integrity');
}
console.log('ok interrupted navigation follows the reader');

const deep = setup({hash: '#retirement'});
deep.window.emit('load'); deep.flush(); deep.later();
deep.scroll(deep.scroller.scrollTop); deep.scroll(deep.scroller.scrollTop); deep.expect('retirement');
deep.location.hash = '#scoring'; deep.window.emit('hashchange'); deep.arrive('scoring'); deep.expect('scoring');
deep.click('retirement', deep.rightLink); deep.arrive('retirement'); deep.expect('retirement');
deep.click('retirement'); deep.arrive('retirement'); deep.expect('retirement');
console.log('ok deep links, history navigation, right TOC, and repeated destinations');

const legacy = setup({hash: '#entrant-states'});
assert.equal(legacy.location.hash, '#retirement');
legacy.window.emit('hashchange'); legacy.arrive('retirement'); legacy.expect('retirement');
console.log('ok legacy anchor redirects');

const bottom = setup();
bottom.headings.retirement.y = bottom.scroller.scrollHeight - 300;
bottom.click('retirement'); bottom.arrive('retirement'); bottom.expect('retirement');
bottom.scroller.emit('wheel'); bottom.scroll(bottom.scroller.scrollTop);
bottom.expect(ids[ids.length - 1]);
console.log('ok clamped destination and manual end-of-page selection');
