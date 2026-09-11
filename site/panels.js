/* Two separators share the space around a flexible benchmark panel. */
(function () {
  'use strict';
  // The board stops at 360: below that a name would have to shrink or be cut, and the user set that as the line.
  const MIN = {tasks: 180, center: 320, board: 360};
  const GUTTERS = 16;
  const COLLAPSED_WIDTH = 44;
  const STORAGE_KEY = 'ssa.panel-widths.v1';
  const COLLAPSED_KEY = 'ssa.tasks-collapsed.v1';
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  function defaults(width) {
    // The chart is the page; the board gets a quarter of the width and never more than 400px.
    return {tasks: clamp(width * .18, MIN.tasks, 252), board: clamp(width * .25, MIN.board, 440)};
  }

  // Shrink both side panels proportionally only when the viewport needs it.
  function fitWidths(width, desired, collapsed = false) {
    const taskMin = collapsed ? COLLAPSED_WIDTH : MIN.tasks;
    const gutters = collapsed ? GUTTERS / 2 : GUTTERS;
    const available = Math.max(taskMin + MIN.board, width - gutters - MIN.center);
    let tasks = collapsed ? taskMin : Math.max(taskMin, desired.tasks), board = Math.max(MIN.board, desired.board);
    if (tasks + board > available) {
      const extra = tasks + board - taskMin - MIN.board;
      const scale = extra ? (available - taskMin - MIN.board) / extra : 0;
      tasks = taskMin + (tasks - taskMin) * scale;
      board = MIN.board + (board - MIN.board) * scale;
    }
    return {tasks, board};
  }

  function mountPanels(root, win = window) {
    if (!root) return;
    const doc = root.ownerDocument;
    const handles = [...root.querySelectorAll('[data-panel-resizer]')];
    const toggle = root.querySelector('[data-task-toggle]');
    const filters = root.querySelector('#task-filter-list');
    const desktop = win.matchMedia('(min-width:881px)');
    let preferred = null, sizes = null, active = null, measuredWidth = 0, collapsed = false;
    try {
      const saved = JSON.parse(win.localStorage.getItem(STORAGE_KEY));
      if (saved && ['tasks', 'board'].every(k => Number.isFinite(saved[k]) && saved[k] > 0 && saved[k] < 1)) {
        preferred = saved;
      }
    } catch (_) { /* Resizing still works when storage is unavailable. */ }
    try { collapsed = win.localStorage.getItem(COLLAPSED_KEY) === 'true'; } catch (_) {}

    const width = () => root.getBoundingClientRect().width;
    const maximum = (side, total = width()) => collapsed && side === 'tasks' ? COLLAPSED_WIDTH
      : Math.max(MIN[side], total - (collapsed ? GUTTERS / 2 : GUTTERS) - MIN.center - sizes[side === 'tasks' ? 'board' : 'tasks']);

    function showTaskState() {
      root.classList.toggle('tasks-collapsed', collapsed);
      filters.hidden = collapsed;
      toggle.setAttribute('aria-expanded', String(!collapsed));
      toggle.setAttribute('aria-label', collapsed ? 'Expand task filters' : 'Collapse task filters');
      toggle.title = collapsed ? 'Expand task filters' : 'Collapse task filters';
      const divider = handles.find(handle => handle.dataset.panelResizer === 'tasks');
      divider.setAttribute('aria-hidden', String(collapsed));
      divider.tabIndex = collapsed ? -1 : 0;
    }

    function apply(next) {
      sizes = next;
      root.style.setProperty('--task-width', next.tasks + 'px');
      root.style.setProperty('--board-width', next.board + 'px');
      handles.forEach(handle => {
        const side = handle.dataset.panelResizer;
        handle.setAttribute('aria-valuemin', collapsed && side === 'tasks' ? COLLAPSED_WIDTH : MIN[side]);
        handle.setAttribute('aria-valuemax', Math.round(maximum(side)));
        handle.setAttribute('aria-valuenow', Math.round(next[side]));
        handle.setAttribute('aria-valuetext', Math.round(next[side]) + ' pixels');
      });
    }

    function save() {
      const total = width();
      // Moving the leaderboard while collapsed must not overwrite the saved
      // expanded Task width with the narrow rail's width.
      preferred = {tasks: collapsed ? (preferred ? preferred.tasks : defaults(total).tasks / total) : sizes.tasks / total,
                   board: sizes.board / total};
      try { win.localStorage.setItem(STORAGE_KEY, JSON.stringify(preferred)); } catch (_) {}
    }

    function finish(cancelled) {
      if (!active) return;
      const drag = active;
      active = null;
      if (cancelled) apply(drag.start);
      else save();
      drag.handle.classList.remove('dragging');
      doc.body.classList.remove('resizing-panels');
      if (drag.handle.hasPointerCapture(drag.id)) drag.handle.releasePointerCapture(drag.id);
    }

    function resize() {
      const total = width();
      // A hidden Leaderboard tab reports zero; keep the user's last usable layout.
      if (!desktop.matches || !total) { finish(true); measuredWidth = 0; return; }
      if (total === measuredWidth && sizes) return;
      finish(true);
      measuredWidth = total;
      apply(fitWidths(total, preferred
        ? {tasks: preferred.tasks * total, board: preferred.board * total}
        : defaults(total), collapsed));
    }

    function reset() {
      if (!desktop.matches) return;
      finish(true);
      preferred = null;
      try { win.localStorage.removeItem(STORAGE_KEY); } catch (_) {}
      apply(fitWidths(width(), defaults(width()), collapsed));
    }

    toggle.addEventListener('click', () => {
      finish(true);
      if (!collapsed && desktop.matches && sizes && width()) save();
      collapsed = !collapsed;
      try { win.localStorage.setItem(COLLAPSED_KEY, String(collapsed)); } catch (_) {}
      showTaskState();
      measuredWidth = 0;
      resize();
      toggle.focus();
    });

    handles.forEach(handle => {
      const side = handle.dataset.panelResizer;
      const direction = side === 'tasks' ? 1 : -1;
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0 || !event.isPrimary || !desktop.matches || active || (collapsed && side === 'tasks')) return;
        resize();
        event.preventDefault();
        handle.focus();
        handle.setPointerCapture(event.pointerId);
        active = {handle, id: event.pointerId, x: event.clientX, start: {...sizes}};
        handle.classList.add('dragging');
        doc.body.classList.add('resizing-panels');
      });
      handle.addEventListener('pointermove', event => {
        if (!active || active.handle !== handle || active.id !== event.pointerId) return;
        const value = active.start[side] + direction * (event.clientX - active.x);
        apply({...sizes, [side]: clamp(value, MIN[side], maximum(side))});
      });
      handle.addEventListener('pointerup', event => {
        if (active && active.handle === handle && active.id === event.pointerId) finish(false);
      });
      for (const name of ['pointercancel', 'lostpointercapture']) {
        handle.addEventListener(name, event => {
          if (active && active.handle === handle && active.id === event.pointerId) finish(true);
        });
      }
      handle.addEventListener('dblclick', reset);
      handle.addEventListener('keydown', event => {
        if (!desktop.matches || (collapsed && side === 'tasks')) return;
        if (event.key === 'Escape') { finish(true); return; }
        if (event.key === 'Enter') { event.preventDefault(); reset(); return; }
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        finish(true);
        const delta = (event.key === 'ArrowRight' ? 1 : -1) * direction * (event.shiftKey ? 40 : 10);
        const value = event.key === 'Home' ? MIN[side] : event.key === 'End' ? maximum(side) : sizes[side] + delta;
        apply({...sizes, [side]: clamp(value, MIN[side], maximum(side))});
        save();
      });
    });
    desktop.addEventListener('change', () => { measuredWidth = 0; resize(); });
    new win.ResizeObserver(resize).observe(root);
    showTaskState();
    resize();
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = {fitWidths, mountPanels};
  else mountPanels(document.getElementById('page-leaderboard'));
})();
