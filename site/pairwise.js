/* Active registered methods. Scores come only from the public round payload. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.SSAPairwise = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  // Explicit method selections for multi-account teams; never choose a team's
  // best answer after resolution. worldVac and Marina are separate methods,
  // not fallbacks for the selected Vac and SuperAgent methods.
  const EXTERNAL = [
    {id: 'apodex-futureflow', name: 'Apodex-FutureFlow'},
    {id: 'h2oai-superagent', name: 'H2O.ai SuperAgent'},
    {id: 'vac-combined', name: 'Vac', members: [
      'vac-public-opinion', 'vac-sentiment-expectations',
      'vac-population-profiles', 'vac-collective-attention'
    ]},
    {id: 'yulan-onesim', name: 'YuLan-OneSim'},
    {id: 'zhengzheng-agent', name: 'ZhengZheng Forecaster'}
  ];
  const EXCLUDED = new Set(['crowd', 'persistence', 'trend', 'ewma', 'climatology',
    'test-jay', 'just4test', 'ssa-testing-team', 'worldvac-base', 'h2oai-marina']);

  function lossOf(score) {
    if (!score) return null;
    const keys = ['crps', 'energy', 'loss'].filter(k => Object.hasOwn(score, k));
    if (keys.length !== 1) return null;
    const value = score[keys[0]];
    return Number.isFinite(value) && value >= 0 ? value : null;
  }

  function rosterOf(data, field) {
    const registered = new Map((data.entrants || []).map(e => [e.entrant_id, e]));
    const answered = new Set();
    for (const r of data.rounds || []) {
      if (r.status && r.status !== 'resolved') continue;
      for (const [id, score] of Object.entries(r.scores || {})) {
        if (lossOf(score) !== null) answered.add(id);
      }
    }
    const active = id => !EXCLUDED.has(id) && !(data.retired || {})[id]
      && (!registered.has(id) || registered.get(id).status !== 'retired');
    const roster = [], consumed = new Set();
    for (const method of EXTERNAL) {
      const members = method.members || [method.id];
      members.forEach(id => consumed.add(id));
      if (members.some(id => registered.has(id) && answered.has(id) && active(id))) roster.push(method);
    }
    // Generated harness variants may not have their own registration record;
    // resolve their type from the longest registered model-id prefix. A retired
    // base registration does not retire a distinct active web variant.
    const modelBases = [...registered.values()].filter(e => e.type === 'llm')
      .sort((a, b) => b.entrant_id.length - a.entrant_id.length);
    for (const id of [...answered].sort()) {
      if (consumed.has(id) || !active(id)) continue;
      const entry = registered.get(id);
      if (entry && entry.type === 'participant') {
        roster.push({id, name: entry.name || id});
      } else if (field === 'mixed' && ((entry && entry.type === 'llm') ||
        (!entry && modelBases.some(e => id.startsWith(e.entrant_id + '-'))))) {
        roster.push({id});
      }
    }
    return roster;
  }

  function build(data, options) {
    options = options || {};
    const field = options.field || 'mixed', scope = options.scope || 'all';
    if (!['mixed', 'external'].includes(field) || !['all', 's0', 'matched'].includes(scope)) {
      throw new Error('Unknown pairwise field or question window');
    }
    const roster = options.roster || rosterOf(data, field);
    const ids = roster.map(m => m.id), accounts = roster.flatMap(m => m.members || [m.id]);
    if (new Set(ids).size !== ids.length || new Set(accounts).size !== accounts.length) {
      throw new Error('An account cannot occupy two comparison slots');
    }
    const since = (data.season0_main || {}).since || '2026-09-30T00:00:00Z';
    const acc = new Map(ids.map(id => [id, {answered: 0, points: 0, top3: 0, skill: 0, skillN: 0}]));
    const roundIds = [], candidates = [], seen = new Set();
    let excluded = 0;
    for (const r of data.rounds || []) {
      if (!r.scores || (r.status && r.status !== 'resolved')) continue;
      if (scope === 's0' && (!r.lock_at || r.lock_at < since)) continue;
      if (seen.has(r.round_id)) throw new Error('Duplicate scored question: ' + r.round_id);
      seen.add(r.round_id);
      const scores = new Map();
      for (const method of roster) {
        const available = (method.members || [method.id]).filter(id => lossOf(r.scores[id]) !== null);
        if (available.length > 1) {
          throw new Error(method.name + ' has multiple answers for ' + r.round_id +
            '; an official method must be selected before ranking this question.');
        }
        if (available.length) scores.set(method.id, r.scores[available[0]]);
      }
      if (scores.size < 2) { excluded++; continue; }
      candidates.push(r.round_id);
      if (scope === 'matched' && scores.size !== roster.length) continue;
      roundIds.push(r.round_id);
      // Sort once and count tied groups: O(m log m), rather than enumerating
      // every pair as the field grows.
      const ordered = [...scores].map(([id, score]) => ({id, score, loss: lossOf(score)}))
        .sort((a, b) => a.loss - b.loss);
      for (let start = 0; start < ordered.length;) {
        let end = start + 1;
        while (end < ordered.length && ordered[end].loss === ordered[start].loss) end++;
        const tied = end - start, n = ordered.length;
        const points = 100 * (n - end + .5 * (tied - 1)) / (n - 1);
        const top3 = Math.max(0, Math.min(tied, Math.min(3, n) - start)) / tied;
        for (let i = start; i < end; i++) {
          const {id, score} = ordered[i], a = acc.get(id);
          a.answered++; a.points += points; a.top3 += top3;
          if (Number.isFinite(score.skill)) { a.skill += score.skill; a.skillN++; }
        }
        start = end;
      }
    }
    const n = roundIds.length;
    const entries = n ? roster.map(method => {
      const a = acc.get(method.id);
      return {entrant: method.id, name: method.name || method.id, members: method.members || [method.id],
        rounds: a.answered, total_rounds: n, answered: a.answered + '/' + n,
        pairwise: a.points / n, top3: a.top3,
        mean_skill: a.skillN ? a.skill / a.skillN : null};
    }).sort((a, b) => b.pairwise - a.pairwise || a.entrant.localeCompare(b.entrant)) : [];
    let rank = 1, rankValue = entries.length ? entries[0].pairwise : 0;
    for (let i = 0; i < entries.length; i++) {
      if (rankValue - entries[i].pairwise > 1e-9) { rank = i + 1; rankValue = entries[i].pairwise; }
      entries[i].rank = rank;
    }
    return {field, scope, since, entries, scored_rounds: n, round_ids: roundIds,
      comparable_rounds: candidates.length, excluded_rounds: excluded, methods: roster.length};
  }
  return {build, lossOf, rosterOf, external: EXTERNAL};
});
