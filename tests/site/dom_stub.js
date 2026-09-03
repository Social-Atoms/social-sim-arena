// A DOM small enough to run site/*.html's own script against real data.json,
// and faithful in the two places that silently produce empty strings if it is
// not: `textContent` -> `innerHTML` escaping (which is how the pages' `esc()`
// is implemented), and `document.createElement` returning a node that does it.
// A stub without those reports every escaped field as blank, which looks
// exactly like a data bug and is not one.
'use strict';

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                  .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

const noopCtx = new Proxy({}, {
  get: (_t, k) => (k === 'measureText' ? () => ({width: 0}) : () => {}),
  set: () => true,
});

function node(tag) {
  const n = {
    tagName: (tag || 'div').toUpperCase(),
    children: [], style: {}, dataset: {}, width: 0, height: 0,
    _text: '', _html: '',
    classList: {toggle() {}, add() {}, remove() {}, contains: () => false},
    appendChild(c) { this.children.push(c); return c; },
    setAttribute() {}, getAttribute: () => null, removeAttribute() {},
    addEventListener() {}, removeEventListener() {},
    querySelector: () => node(), querySelectorAll: () => [],
    getContext: () => noopCtx,
    getBoundingClientRect: () => ({width: 600, height: 200, top: 0, left: 0}),
  };
  // One lazily-created parent, memoised. Returning a fresh node each time
  // would let a page that walks upward recurse forever.
  let parent = null;
  Object.defineProperty(n, 'parentElement', {
    get() { if (!parent) { parent = node('div'); } return parent; },
  });
  Object.defineProperty(n, 'parentNode', {get() { return n.parentElement; }});
  Object.defineProperty(n, 'firstChild',
                        {get() { return n.children[0] || null; }});
  Object.defineProperty(n, 'lastChild',
                        {get() { return n.children[n.children.length - 1] || null; }});
  Object.defineProperty(n, 'textContent', {
    get() { return this._text; },
    // This is the whole point of the stub: the pages escape by writing text
    // into a detached div and reading its HTML back.
    set(v) { this._text = String(v); this._html = escapeHtml(v); },
  });
  Object.defineProperty(n, 'innerHTML', {
    get() { return this._html; },
    set(v) { this._html = String(v); this._text = String(v).replace(/<[^>]*>/g, ''); },
  });
  return n;
}

function makeDom() {
  const els = {};
  const document = {
    getElementById(id) {
      if (!els[id]) { els[id] = node(); els[id].id = id; }
      return els[id];
    },
    createElement: (tag) => node(tag),
    querySelector: () => node(),
    querySelectorAll: () => [],
    body: node('body'),
  };
  return {els, document};
}

function installGlobals(extra) {
  const {els, document} = makeDom();
  const noop = () => {};
  const observer = function () {
    return {observe: noop, unobserve: noop, disconnect: noop};
  };
  const win = {
    __data: null, __tab: 'agg', __board: 'all',
    innerWidth: 1200, innerHeight: 900, devicePixelRatio: 1,
    location: {hash: '', href: 'https://example.test/', search: ''},
    history: {replaceState: noop, pushState: noop},
    addEventListener: noop, removeEventListener: noop, scrollTo: noop,
    matchMedia: () => ({matches: false, addEventListener: noop,
                        addListener: noop}),
    getComputedStyle: () => ({getPropertyValue: () => ''}),
  };
  // Some of these (`navigator`, `fetch`) are getter-only on modern Node, so
  // they are defined rather than assigned.
  const define = (obj) => {
    for (const [k, v] of Object.entries(obj)) {
      try { global[k] = v; }
      catch (e) {
        Object.defineProperty(global, k, {value: v, configurable: true,
                                          writable: true});
      }
    }
  };
  define(Object.assign({
    document, window: win,
    location: win.location, history: win.history,
    addEventListener: noop, removeEventListener: noop,
    // Time and animation are stubbed to no-ops rather than fired: this checks
    // one render, and a real timer would either hang the process or redraw
    // into assertions that have already been made.
    setTimeout: () => 0, clearTimeout: noop,
    setInterval: () => 0, clearInterval: noop,
    requestAnimationFrame: () => 0, cancelAnimationFrame: noop,
    matchMedia: win.matchMedia, getComputedStyle: win.getComputedStyle,
    ResizeObserver: observer, IntersectionObserver: observer,
    MutationObserver: observer,
    // Never reached: a render check that fetches passes for the wrong reason
    // behind a proxy and fails on a plane.
    fetch: () => Promise.reject(new Error('the render check is offline')),
    navigator: {userAgent: 'ssa-render-check', language: 'en-US'},
    Image: function () { return node('img'); },
  }, extra || {}));
  return {els, document, window: win};
}

// Values that mean a render went wrong and are visible to whoever opens the
// page. `null` is matched on a word boundary: the pages legitimately say
// "the statistical nulls", and a substring match calls that a bug.
const LEAK = [
  [/\bundefined\b/g, 'undefined'],
  [/\bNaN\b/g, 'NaN'],
  [/\bnull\b/g, 'null'],
  [/\[object Object\]/g, '[object Object]'],
  [/Invalid Date/g, 'Invalid Date'],
];

function leaks(els) {
  let all = '';
  for (const el of Object.values(els)) {
    all += (el.innerHTML || '') + ' ';
    for (const c of el.children || []) all += (c.innerHTML || '') + ' ';
  }
  const found = [];
  for (const [re, name] of LEAK) {
    for (const m of all.matchAll(re)) {
      const i = m.index;
      found.push({
        what: name,
        near: all.slice(Math.max(0, i - 70), i + 40)
                 .replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim(),
      });
    }
  }
  return found;
}

module.exports = {node, makeDom, installGlobals, escapeHtml, leaks};
