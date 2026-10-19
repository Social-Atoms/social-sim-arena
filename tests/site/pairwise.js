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

// The running curves share the exact scoring pass with the final standings.
{
  const rr=[
    {...round('later',{b:sc(1),c:sc(2)}),release_at:'2026-10-03T00:00:00Z'},
    {...round('first',{a:sc(1),b:sc(2),c:sc(2)}),release_at:'2026-10-01T00:00:00Z'},
    {...round('same-day',{a:sc(3),b:sc(2),c:sc(1)}),release_at:'2026-10-01T12:00:00Z'}];
  const b=build(rr),traj=b.trajectory;
  assert.deepEqual(traj.map(t=>[t.date,t.n]),[['2026-10-01',2],['2026-10-03',3]]);
  near(traj[0].pairwise.a,50); near(traj[1].pairwise.a,100/3);
  for(const e of b.entries){
    near(traj.at(-1).pairwise[e.entrant],e.pairwise);
    near(traj.at(-1).skills[e.entrant],e.mean_skill);
    near(traj.at(-1).crps[e.entrant],e.mean_crps);
  }
  assert.equal(build(rr,'matched').trajectory.at(-1).n,2);
  assert.deepEqual(build([]).trajectory,[]);
  assert.equal(build(rr.slice(0,1)).trajectory.length,1);
  assert.deepEqual(build([...rr].reverse()).trajectory,traj);
}
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
  // A revoked priority account cannot supply the team's answer.
  const revoked=JSON.parse(JSON.stringify(data));
  revoked.entrants.find(e=>e.entrant_id==='anon-a1').status='revoked';
  revoked.rounds=[rr[0]];
  near(byId(pw.build(revoked))['anonymous-combined'].pairwise,100);
  // More accounts cannot manufacture a contested question or shrink Common.
  assert(!board.round_ids.includes('only-anon'));
  for(const scope of ['s0','matched']) {
    const b=pw.build(data,{scope});assert.equal(b.methods,1);
    assert(!b.entries.some(e=>e.entrant==='anonymous-combined'||anon.members.includes(e.entrant)));
  }
}
// Registered statistical references and Crowd compete normally, with shared coverage rules.
{
  const refs=['persistence','trend','ewma','climatology'];
  const data={entrants:[...refs.map(id=>({entrant_id:id,name:id,type:'baseline'})),
    {entrant_id:'crowd',type:'baseline'},{entrant_id:'team',type:'participant'}],
    rounds:[round('number',{team:sc(2),persistence:sc(1),trend:sc(3),ewma:sc(4),climatology:sc(5),crowd:sc(0)}),
      round('profile',{team:{energy:1,skill:.5},persistence:{energy:2,skill:0}})]};
  const b=pw.build(data),rows=byId(b);
  assert.equal(b.methods,6);assert(rows.crowd);assert.equal(rows.trend.answered,'1/2');
  near(rows.persistence.pairwise,40);near(rows.team.pairwise,80);near(rows.crowd.pairwise,50);
  assert.equal(pw.build(data,{scope:'matched'}).scored_rounds,1);
  assert.equal(pw.build(data,{field:'external'}).methods,1);
  const end=b.trajectory.at(-1);for(const e of b.entries)near(end.pairwise[e.entrant],e.pairwise);
}
// Each team's two methods stay independent; neither gets absorbed by the other slot.
{
  const ids=['h2oai-superagent','h2oai-marina','vac-public-opinion','worldvac-base'];
  const data={entrants:ids.map(entrant_id=>({entrant_id,type:'participant',name:entrant_id})),
    rounds:[round('separate',Object.fromEntries(ids.map((id,i)=>[id,sc(i+1)])))]};
  for(const scope of ['all','s0','matched']){
    const b=pw.build(data,{scope}),rows=byId(b);
    assert.equal(b.methods,4);assert.equal(b.scored_rounds,1);
    assert.deepEqual(Object.keys(rows).sort(),['h2oai-marina','h2oai-superagent','vac-combined','worldvac-base']);
    near(rows['h2oai-superagent'].pairwise,100);near(rows['h2oai-marina'].pairwise,200/3);
    near(rows['vac-combined'].pairwise,100/3);near(rows['worldvac-base'].pairwise,0);
    for(const id of Object.keys(rows))near(b.trajectory.at(-1).pairwise[id],rows[id].pairwise);
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
    {entrant_id:'ssa-testing-team',type:'participant'}],
    retired:{'old-model-web':{}},rounds:[round('new',{'new-team':sc(1),'new-model-web':sc(2),
      'new-model':sc(3),'old-model-web':sc(0),'retired-team':sc(0),'revoked-team':sc(0),
      'ssa-testing-team':sc(0),crowd:sc(0)})]};
  assert.deepEqual(pw.rosterOf(data,'external').map(x=>x.id),['new-team']);
  assert.deepEqual(pw.rosterOf(data,'mixed').map(x=>x.id),['new-model','new-model-web','new-team']);
  assert.equal(pw.build(data).methods,3);
  data.entrants.push({entrant_id:'later-team',type:'participant'});
  data.rounds.push(round('later',{'new-team':sc(1),'later-team':sc(2)}));
  assert.equal(pw.build(data,{field:'external'}).methods,2);
  assert.deepEqual(pw.build(data,{field:'external',scope:'matched'}).round_ids,[]);
  assert.equal(pw.build(data,{scope:'s0'}).methods,0);
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
const {installGlobals, leaks, node}=require('./dom_stub.js');
const {els}=installGlobals();
document.documentElement=node('html');
const html=fs.readFileSync(path.join(root,'site/index.html'),'utf8');
assert(html.includes('<script src="pairwise.js"></script>'));
const script=(html.match(/<script>([\s\S]*?)<\/script>/g)||[])
  .map(b=>b.replace(/^<script>/,'').replace(/<\/script>$/,'')).join('\n');
vm.runInThisContext(script,{filename:'index-inline.js'});
const latestPath=process.argv[2] || path.join(root,'site/data.json');
const published=JSON.parse(fs.readFileSync(latestPath,'utf8'));
window.__data=published;window.__tab='agg';

// Freeze eligibility before scoring: new teams, aliases, model variants and
// baselines cannot alter S0/Common opponents, scores, curves or intersection.
{
  const fixture=JSON.parse(JSON.stringify(published));
  const late=['njusim-mace','socialai-x','jev-direct','jev-persona','xixi-social',
    ...Array.from({length:6},(_,i)=>'anon-a'+(i+1))];
  fixture.entrants.push(...late.map(entrant_id=>({entrant_id,name:entrant_id,type:'participant'})),
    {entrant_id:'late-model',name:'Late model',type:'llm'},{entrant_id:'late-baseline',name:'Late baseline',type:'baseline'});
  const laterIds=[...late,'late-model-web','late-baseline','gemini-pro-web-late-arm'];
  // Perfect, fully covered newcomers still get no Season 0 eligibility.
  for(const r of fixture.rounds)if(r.status==='resolved'&&r.scores){
    for(const id of laterIds)r.scores[id]=sc(0,1);
  }
  assert.equal(pw.season0Methods.length,24);
  assert.equal(new Set(pw.season0Methods).size,24);
  assert(Object.isFrozen(pw.season0Methods));
  for(const scope of ['s0','matched']) {
    const original=pw.build(published,{scope});
    assert.deepEqual(pw.build(fixture,{scope}),original);
    for(const ranking of ['pairwise','skill']) {
      window.__data=fixture;window.__ranking=ranking;window.__pairwiseScope=scope;
      renderBoard('all');
      assert.deepEqual(window.__pairwiseBoard,original);
      const body=els[ranking==='pairwise'?'pairwise-body':'lb-body'].innerHTML;
      for(const id of laterIds)assert(!body.includes('data-entrant="'+id+'"'));
    }
  }
  const all=pw.build(fixture);
  for(const id of ['njusim-mace','socialai-x','jev-direct','jev-persona','xixi-social',
    'anonymous-combined','late-model-web','late-baseline','gemini-pro-web-late-arm'])
    assert(all.entries.some(e=>e.entrant===id),id+' missing from All');
  // A legitimate future round answered only by existing methods still enters
  // Common; withholding every late method's answer cannot remove it.
  const old=pw.rosterOf(published,'mixed','matched');
  const scores=Object.fromEntries(old.map((m,i)=>[(m.members||[m.id])[0],sc(i+1)]));
  const future=round('future-cohort-only',scores,'2026-10-26T00:00:00Z');
  fixture.rounds.push(future);
  const control={...published,rounds:[...published.rounds,future]};
  for(const scope of ['s0','matched']) {
    assert.deepEqual(pw.build(fixture,{scope}),pw.build(control,{scope}));
    assert(pw.build(fixture,{scope}).round_ids.includes('future-cohort-only'));
  }
  window.__data=published;
}

for (const field of ['mixed','external']) for (const scope of ['all','s0','matched']) {
  window.__ranking='pairwise';window.__pairwiseField=field;window.__pairwiseScope=scope;
  renderBoard('all');
  assert(!els['pairwise-table'].hidden);assert(els['legacy-leaderboard'].hidden);
  const got=window.__pairwiseBoard;assert(got,els['pairwise-body'].textContent);
  assert.equal(got.field,'mixed');assert.equal(got.scope,scope);
  for(const id of ['h2oai-marina','worldvac-base'])assert(got.entries.some(e=>e.entrant===id));
  assert(!els['pairwise-body'].innerHTML.includes('#entrant/vac-combined'));
  assert(els['pairwise-body'].innerHTML.includes('Crowd'));
}
// Metric switches preserve the exact entrant set, metadata and question set.
for(const scope of ['all','s0','matched']){
  window.__pairwiseScope=scope;window.__ranking='pairwise';renderBoard('all');
  const board=window.__pairwiseBoard;
  const extract=body=>Object.fromEntries([...body.matchAll(/<tr data-entrant="([^"]+)"[^>]*><td[^>]*>.*?<\/td><td>([\s\S]*?)<\/td>/g)].map(m=>[m[1],m[2]]));
  const pair=extract(els['pairwise-body'].innerHTML);
  assert.equal(Object.keys(pair).length,board.entries.length);
  assert(!els['pairwise-body'].innerHTML.includes('Top 3'));
  assert(!els['pairwise-body'].innerHTML.includes('Skill '));
  assert.equal(els['lb-title'].textContent,'Pairwise win rate');
  window.__ranking='skill';renderBoard('all');
  assert.deepEqual(extract(els['lb-body'].innerHTML),pair);
  assert.deepEqual(window.__pairwiseBoard.round_ids,board.round_ids);
  assert.equal(els['lb-title'].textContent,'Original skill');
  for(const id of ['just4test','test-jay','ssa-testing-team'])assert(!(id in pair));
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
  assert(standingIdentity(window.__pairwiseBoard.entries.find(e=>e.entrant==='anonymous-combined'),fixture).includes('Participant'));
  assert(!standingIdentity(window.__pairwiseBoard.entries.find(e=>e.entrant==='anonymous-combined'),fixture).includes('Season 0'));
  for(const scope of ['s0','matched']) {
    window.__pairwiseScope=scope;renderBoard('s0');
    assert(!els['pairwise-body'].innerHTML.includes('Anonymous'));
  }
  fixture.season0_main.entries.push({entrant:'anon-a1',rounds:1,mean_skill:10,mean_crps:0});
  window.__ranking='skill';renderBoard('s0');
  assert(!els['lb-body'].innerHTML.includes('#entrant/anon-a1'));
  window.__data=published;window.__ranking='pairwise';
}
// All/S0/Common endpoints and roster match the corresponding right-hand board.
for(const scope of ['all','s0','matched']){
  const b=pw.build(published,{scope});
  if(!b.entries.length)continue;
  const end=b.trajectory.at(-1);
  assert(trajIds(b.trajectory).includes('persistence'));
  assert.equal(end.n,b.scored_rounds);
  assert.deepEqual(Object.keys(end.pairwise).sort(),b.entries.map(e=>e.entrant).sort());
  for(const e of b.entries)near(end.pairwise[e.entrant],e.pairwise);
}
// New visitors and the bare navigation entry open S0 Pairwise on both sides.
for(const hash of ['', '#leaderboard']){
  location.hash=hash;window.__ranking='skill';window.__unitAgg='crps';
  window.__board='all';window.__pairwiseScope='matched';applyHash();
  assert.equal(window.__board,'s0');assert.equal(window.__range,-1);
  assert.equal(window.__pairwiseScope,'s0');assert.equal(window.__ranking,'pairwise');
  assert.equal(window.__unitAgg,'pairwise');assert.equal(window.__pairwiseBoard.scope,'s0');
}
location.hash='#leaderboard/agg/all';applyHash();
assert.equal(window.__board,'all');assert.equal(window.__pairwiseScope,'all');
// All must stay explicit in shared URLs; it can no longer collapse into #leaderboard.
let savedHash='';const replaceState=history.replaceState;
history.replaceState=(_state,_title,hash)=>{savedHash=hash;};syncHash();
assert(savedHash.startsWith('#leaderboard/agg/all'));
history.replaceState=replaceState;
// Bounded axis, no persistence/oracle references, and a useful one-point chart.
{
  const svg=node('svg'),tip=node();svg.clientWidth=800;svg.clientHeight=360;
  const chartPane=document.getElementById('chartpane'),originalQuery=chartPane.querySelector;
  chartPane.querySelector=sel=>sel==='svg'?svg:tip;
  window.__hidden=new Set();window.__keep=new Set(['a','b']);window.__seenIds=new Set(['a','b']);
  const traj=[{date:'2026-10-01',n:1,pairwise:{a:100,b:0},skills:{},crps:{},live:true}];
  aggChart(traj,{a:'A',b:'B'},'pairwise');
  assert(!/NaN|undefined|oracle|persistence/.test(svg.innerHTML));
  assert(svg.innerHTML.includes('100.00'));assert(svg.innerHTML.includes('0.00'));
  assert.equal((svg.innerHTML.match(/<circle /g)||[]).length,2);
  chartPane.querySelector=originalQuery;
}
// Right-to-left synchronization is one-way; chart-only choices survive shared URLs.
window.__data=published;window.__tab='agg';window.__pairwiseScope='all';
setRanking('skill'); assert.equal(window.__unitAgg,'arena');
setChartUnit('pairwise'); assert.equal(window.__ranking,'skill');
assert(chartUrlState().includes('unit=pairwise'));assert(chartUrlState().includes('ranking=skill'));
setRanking('pairwise');assert.equal(window.__unitAgg,'pairwise');
setChartUnit('crps');assert.equal(window.__ranking,'pairwise');
assert(chartUrlState().includes('unit=crps'));
for(const [hash,unit,ranking] of [
  ['#leaderboard','pairwise','pairwise'],
  ['#leaderboard/agg/all?ranking=skill','arena','skill'],
  ['#leaderboard/agg/all?ranking=skill&unit=pairwise','pairwise','skill'],
  ['#leaderboard/agg/all?unit=arena','arena','pairwise']]){
  location.hash=hash;applyHash();
  assert.equal(window.__unitAgg,unit);assert.equal(window.__ranking,ranking);
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
els['common-questions'].onclick();
assert.equal(window.__ranking,'skill');
assert.equal(window.__pairwiseBoard.scope,'matched');
assert(els['pairwise-table'].hidden);assert(!els['legacy-leaderboard'].hidden);
window.__ranking='pairwise';window.__tab='civiqs-approval';renderBoard('all');
assert(els['pairwise-controls'].hidden);assert(els['pairwise-table'].hidden);
assert.deepEqual(leaks(els),[]);
console.log('pairwise calculation, cohort/window switching, and page rendering pass');
process.exit(0);
