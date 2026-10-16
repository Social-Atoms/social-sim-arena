/* A fixed comparison field. Scores come only from the public round payload. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.SSAPairwise = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  // The proposed field for this leaderboard version; never choose a team's
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
  const MODELS = [
    'claude-sonnet-web', 'claude-sonnet-web-superfc',
    'deepseek-pro-web', 'deepseek-pro-web-superfc',
    'gemini-pro-web', 'gemini-pro-web-superfc',
    'glm-web', 'glm-web-superfc',
    'gpt-5.6-luna-web', 'gpt-5.6-luna-web-superfc',
    'grok-web', 'grok-web-superfc'
  ].map(id => ({id}));

  function lossOf(score) {
    if (!score) return null;
    const keys = ['crps', 'energy', 'loss'].filter(k => Object.hasOwn(score, k));
    if (keys.length !== 1) return null;
    const value = score[keys[0]];
    return Number.isFinite(value) && value >= 0 ? value : null;
  }

  function build(data, options) {
    options = options || {};
    const field = options.field || 'mixed', scope = options.scope || 'all';
    if (!['mixed', 'external'].includes(field) || !['all', 's0', 'matched'].includes(scope)) {
      throw new Error('Unknown pairwise field or question window');
    }
    const roster = options.roster || EXTERNAL.concat(field === 'mixed' ? MODELS : []);
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
      for (const [id, score] of scores) {
        const own = lossOf(score), values = [...scores.values()].map(lossOf);
        const better = values.filter(v => v < own).length;
        const tied = values.filter(v => v === own).length;
        const worse = values.length - better - tied;
        const a = acc.get(id);
        a.answered++;
        a.points += 100 * (worse + .5 * (tied - 1)) / (values.length - 1);
        a.top3 += Math.max(0, Math.min(tied, Math.min(3, values.length) - better)) / tied;
        if (Number.isFinite(score.skill)) { a.skill += score.skill; a.skillN++; }
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
    for (const e of entries) {
      e.rank = 1 + entries.filter(other => other.pairwise > e.pairwise + 1e-9).length;
    }
    return {field, scope, since, entries, scored_rounds: n, round_ids: roundIds,
      comparable_rounds: candidates.length, excluded_rounds: excluded, methods: roster.length};
  }
  return {build, lossOf, external: EXTERNAL, models: MODELS};
});
