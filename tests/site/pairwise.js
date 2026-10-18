'use strict';
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const pw = require('../../site/pairwise.js');
const roster = ['a', 'b', 'c'].map(id => ({id}));
const round = (id, scores, lock = '2026-10-01T00:00:00Z') =>
  ({round_id:id, lock_at:lock, status:'resolved', scores});
const sc = (loss, skill = .2) => ({crps:loss, skill});
const build = (rounds, scope='all', methods=roster) => pw.build({rounds}, {scope, roster:methods});
const byId = board => Object.fromEntries(board.entries.map(e => [e.entrant,e]));
const near = (a,b) => assert(Math.abs(a-b)<1e-8, `${a} != ${b}`);

// Every round has the same weight; missing is zero and is not a beaten opponent.
{
  const rr=[round('one',{a:sc(1),b:sc(2),c:sc(3)}), round('two',{b:sc(1),c:sc(2)})];
  const rows=byId(build(rr));
  near(rows.a.pairwise,50); near(rows.b.pairwise,75); near(rows.c.pairwise,0);
  assert.equal(rows.a.answered,'1/2'); near(rows.a.mean_skill,.2);
  assert.deepEqual(build(rr,'matched').round_ids,['one']);
}
// Ties are half-wins; tied top-three boundary slots are shared.
{
  const methods=['a','b','c','d','e'].map(id=>({id}));
  const scores={a:sc(1),b:sc(2),c:sc(3),d:sc(3),e:sc(5)};
  const rows=byId(build([round('tie',scores)],'all',methods));
  near(rows.c.pairwise,37.5); near(rows.c.top3,.5);
  near(Object.values(rows).reduce((s,r)=>s+r.pairwise,0),250);
  near(Object.values(rows).reduce((s,r)=>s+r.top3,0),3);
  assert.equal(rows.c.rank,3);assert.equal(rows.d.rank,3);
}
// Shapes are ranked on raw loss, not skill, and open/invalid/uncontested rounds do not count.
{
  const rr=[round('numeric',{a:sc(1,-99),b:sc(2,99)}),
    round('profile',{a:{energy:5},b:{energy:6}}),
    round('ranking',{a:{loss:.1},b:{loss:.2}}),
    round('single',{a:sc(1)}),round('invalid',{a:sc(NaN),b:sc(1)}),
    {...round('open',{a:sc(1),b:sc(2)}),status:'open'}];
  const board=build(rr);assert.equal(board.scored_rounds,3);
  near(byId(board).a.pairwise,100);near(byId(board).a.mean_skill,-99);
  assert.equal(pw.lossOf({crps:null}),null);
  assert.equal(pw.lossOf({crps:1,energy:2}),null);
  assert.deepEqual(build(rr,'matched').entries,[]);
}
// S0 boundary is inclusive and compares locks, not resolution time.
{
  const scores={a:sc(1),b:sc(2)};
  const b=build([round('old',scores,'2026-09-29T23:59:59Z'),
    round('start',scores,'2026-09-30T00:00:00Z')],'s0');
  assert.deepEqual(b.round_ids,['start']);
}
// Vac specialists form one slot, without worldVac fallback or hindsight best-of selection.
{
  const methods=[pw.external.find(m=>m.id==='vac-combined'),{id:'a'}];
  const rr=[round('public',{'vac-public-opinion':sc(1),a:sc(2),
    'worldvac-base':sc(0),crowd:sc(0),persistence:sc(0)}),
    round('attention',{'vac-collective-attention':{loss:.2},a:{loss:.1}}),
    round('only-worldvac',{'worldvac-base':sc(0),a:sc(2)})];
  const b=build(rr,'all',methods);assert.equal(b.scored_rounds,2);
  near(byId(b)['vac-combined'].pairwise,50);
  assert.throws(()=>build([round('duplicate',{'vac-public-opinion':sc(1),
    'vac-sentiment-expectations':sc(0),a:sc(2)})],'all',methods),/multiple answers/);
  assert.throws(()=>build(rr,'all',[...methods,{id:'vac-public-opinion'}]),/two comparison slots/);
}
// Anonymous occupies one All-only slot; priority never selects by score.
{
  const anon=pw.external.find(m=>m.id==='anonymous-combined');
  const entrants=anon.members.map(entrant_id=>({entrant_id,type:'participant'}));
  entrants.push({entrant_id:'apodex-futureflow',type:'participant'});
  const rr=[round('a1',{'anon-a1':sc(9,-.9),'anon-a2':sc(0,.9),'apodex-futureflow':sc(3)}),
    round('a4',{'anon-a4':sc(.1,.4),'apodex-futureflow':sc(2)}),
    round('a2',{'anon-a2':sc(5,-.2),'anon-a3':sc(0,.8),'apodex-futureflow':sc(3)}),
    round('invalid-a1',{'anon-a1':sc(NaN),'anon-a2':sc(.5,.3),'apodex-futureflow':sc(2)}),
    round('only-anon',Object.fromEntries(anon.members.map(id=>[id,sc(1)])))];
  const data={entrants,rounds:rr};
  const board=pw.build(data);assert.equal(board.methods,2);assert.equal(board.scored_rounds,4);
  assert.equal(board.entries.filter(e=>e.entrant==='anonymous-combined').length,1);
  assert(!board.entries.some(e=>anon.members.includes(e.entrant)));
  near(byId(board)['anonymous-combined'].pairwise,50);
  near(byId(board)['anonymous-combined'].mean_skill,(-.9+.4-.2+.3)/4);
  // More accounts cannot manufacture a contested question or shrink Common.
  assert(!board.round_ids.includes('only-anon'));
  for(const scope of ['s0','matched']) {
    const b=pw.build(data,{scope});assert.equal(b.methods,1);
    assert(!b.entries.some(e=>e.entrant==='anonymous-combined'||anon.members.includes(e.entrant)));
  }
}
// Switching cohorts re-ranks each question, not merely filters a mixed leaderboard.
{
  const rr=[round('x',{a:sc(1),b:sc(3),c:sc(2)})];
  near(byId(build(rr)).c.pairwise,50);
  near(byId(build(rr,'all',[{id:'b'},{id:'c'}])).c.pairwise,100);
}
// Newly scored registered entrants and model variants join automatically.
{
  const data={entrants:[{entrant_id:'new-team',name:'New team',type:'participant'},
    {entrant_id:'new-model',type:'llm'}, {entrant_id:'waiting',type:'participant'},
    {entrant_id:'retired-team',type:'participant',status:'retired'},
    {entrant_id:'revoked-team',type:'participant',status:'revoked'},
    {entrant_id:'ssa-testing-team',type:'participant'},
    {entrant_id:'worldvac-base',type:'participant'}, {entrant_id:'h2oai-marina',type:'participant'}],
    retired:{'old-model-web':{}},rounds:[round('new',{'new-team':sc(1),'new-model-web':sc(2),
      'new-model':sc(3),'old-model-web':sc(0),'retired-team':sc(0),'revoked-team':sc(0),
      'ssa-testing-team':sc(0),'worldvac-base':sc(0),'h2oai-marina':sc(0),crowd:sc(0)})]};
  assert.deepEqual(pw.rosterOf(data,'external').map(x=>x.id),['new-team']);
  assert.deepEqual(pw.rosterOf(data,'mixed').map(x=>x.id),['new-model','new-model-web','new-team']);
  assert.equal(pw.build(data).methods,3);
  data.entrants.push({entrant_id:'later-team',type:'participant'});
  data.rounds.push(round('later',{'new-team':sc(1),'later-team':sc(2)}));
  assert.equal(pw.build(data,{field:'external'}).methods,2);
  assert.deepEqual(pw.build(data,{field:'external',scope:'matched'}).round_ids,['later']);
}
assert.equal(pw.external.length,6);
assert(!pw.external.some(m=>['worldvac-base','h2oai-marina','crowd'].includes(m.id)));
// A growing, tied field agrees with explicit pair enumeration.
{
  const methods=Array.from({length:101},(_,i)=>({id:'entry-'+i}));
  const scores=Object.fromEntries(methods.map((m,i)=>[m.id,sc(i%13)]));
  const rows=byId(build([round('large',scores)],'all',methods));
  for(const m of methods){
    const own=scores[m.id].crps;
    const wins=methods.filter(x=>x.id!==m.id&&scores[x.id].crps>own).length;
    const ties=methods.filter(x=>x.id!==m.id&&scores[x.id].crps===own).length;
    near(rows[m.id].pairwise,100*(wins+.5*ties)/100);
  }
  near(Object.values(rows).reduce((s,r)=>s+r.top3,0),3);
}

// Run the actual landing-page renderer in the existing DOM harness.
const root = path.resolve(__dirname,'../..');
const {installGlobals, leaks}=require('./dom_stub.js');
const {els}=installGlobals();
const html=fs.readFileSync(path.join(root,'site/index.html'),'utf8');
assert(html.includes('<script src="pairwise.js"></script>'));
const script=(html.match(/<script>([\s\S]*?)<\/script>/g)||[])
  .map(b=>b.replace(/^<script>/,'').replace(/<\/script>$/,'')).join('\n');
vm.runInThisContext(script,{filename:'index-inline.js'});
const latestPath=process.argv[2] || path.join(root,'site/data.json');
const published=JSON.parse(fs.readFileSync(latestPath,'utf8'));
window.__data=published;window.__tab='agg';
for (const field of ['mixed','external']) for (const scope of ['all','s0','matched']) {
  window.__ranking='pairwise';window.__pairwiseField=field;window.__pairwiseScope=scope;
  renderBoard('all');
  assert(!els['pairwise-table'].hidden);assert(els['legacy-leaderboard'].hidden);
  const got=window.__pairwiseBoard;assert(got,els['pairwise-body'].textContent);
  assert.equal(got.field,'mixed');assert.equal(got.scope,scope);
  assert(!els['pairwise-body'].innerHTML.includes('#entrant/vac-combined'));
  assert(!els['pairwise-body'].innerHTML.includes('Crowd'));
}
// The visible board also groups Anonymous and keeps it out of S0/Common.
{
  const fixture=JSON.parse(JSON.stringify(published));
  const anon=pw.external.find(m=>m.id==='anonymous-combined');
  fixture.entrants.push(...anon.members.map(entrant_id=>({entrant_id,type:'participant',name:entrant_id})));
  const scored=fixture.rounds.find(r=>r.status==='resolved'&&Object.keys(r.scores||{}).length>2);
  assert(scored);scored.scores['anon-a1']=sc(100,-1);scored.scores['anon-a2']=sc(0,1);
  window.__data=fixture;window.__ranking='pairwise';window.__pairwiseScope='all';renderBoard('all');
  assert.equal((els['pairwise-body'].innerHTML.match(/<b class="model-name" title="A1/g)||[]).length,1);
  assert(!els['pairwise-body'].innerHTML.includes('#entrant/anon-a'));
  for(const scope of ['s0','matched']) {
    window.__pairwiseScope=scope;renderBoard('s0');
    assert(!els['pairwise-body'].innerHTML.includes('Anonymous'));
  }
  fixture.season0_main.entries.push({entrant:'anon-a1',rounds:1,mean_skill:10,mean_crps:0});
  window.__ranking='skill';renderBoard('s0');
  assert(!els['lb-body'].innerHTML.includes('#entrant/anon-a1'));
  window.__data=published;window.__ranking='pairwise';
}
assert(!html.includes('External only'));
for(const id of ['common-help','pairwise-help','pairwise-column-help']) {
  assert(html.includes('aria-describedby="'+id+'"'));
  assert(html.includes('id="'+id+'" role="tooltip"'));
}
assert(!html.includes('data-pw-scope'));
assert(html.indexOf('id="common-questions"')>html.indexOf('>S0</button>'));
window.__pairwiseScope=undefined;renderBoard('s0');
assert.equal(window.__pairwiseBoard.scope,'s0');
els['common-questions'].onclick();
assert.equal(window.__pairwiseBoard.scope,'matched');
assert(!els['common-questions'].hidden);
assert(chartUrlState().includes('pw=matched'));
assert(!chartUrlState().includes('field='));
window.__ranking='skill';renderBoard('s0');
assert(els['pairwise-table'].hidden);assert(!els['legacy-leaderboard'].hidden);
window.__ranking='pairwise';window.__tab='civiqs-approval';renderBoard('all');
assert(els['pairwise-controls'].hidden);assert(els['pairwise-table'].hidden);
assert.deepEqual(leaks(els),[]);
console.log('pairwise calculation, cohort/window switching, and page rendering pass');
process.exit(0);
