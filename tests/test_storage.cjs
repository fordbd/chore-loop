// Exercises the actual browser storage adapter without third-party dependencies.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('dist/index.html','utf8');
const source=html.slice(html.indexOf('function persist(){'),html.indexOf('\nfunction el('));
function harness(initial=null){
  const shared={revision:initial?3:0,state:initial},elements={status:{textContent:''},editor:{open:false},print:{disabled:false}},main={inert:false},timers=[];
  const ctx={state:{version:1,kids:[],chores:[],checks:{}},serverStorage:true,serverRevision:0,pendingWrites:0,writeQueue:Promise.resolve(),syncFailed:false,storageOK:true,
    $:id=>elements[id],document:{hidden:false,querySelector:()=>main},structuredClone,valid:()=>true,clean:s=>s,settings:()=>{},render:()=>{},setInterval:fn=>timers.push(fn),localStorage:{setItem:()=>{throw Error('Hosted app must not use browser storage')}},
    fetch:async(url,options={})=>{if(options.method==='PUT'){const body=JSON.parse(options.body);if(body.revision!==shared.revision)return {ok:false,json:async()=>({error:'Another device changed this chart'})};shared.revision++;shared.state=body.state;return {ok:true,json:async()=>({revision:shared.revision})}}return {ok:true,json:async()=>structuredClone(shared)}}};
  vm.createContext(ctx);vm.runInContext(source,ctx);return {ctx,shared,elements,main,timers};
}
(async()=>{
  let h=harness();await h.ctx.boot();assert.equal(h.shared.revision,1);assert.equal(h.main.inert,false);assert.equal(h.elements.print.disabled,false);
  h.ctx.state.checks.a=true;const one=h.ctx.persist();h.ctx.state.checks.b=true;const two=h.ctx.persist();assert(await one);assert(await two);assert.equal(h.shared.revision,3);assert.equal(h.shared.state.checks.a,true);assert.equal(h.shared.state.checks.b,true);
  h.shared.revision++;h.shared.state.checks.remote=true;await h.timers[0]();assert.equal(h.ctx.state.checks.remote,true);
  h.elements.editor.open=true;h.shared.revision++;h.shared.state.checks.other=true;await h.timers[0]();assert.equal(h.ctx.state.checks.other,undefined);h.ctx.state.checks.unsaved=true;assert.equal(await h.ctx.persist(),false);assert.equal(h.ctx.syncFailed,true);assert(h.elements.status.textContent.includes('NOT SAVED'));assert.equal(h.shared.state.checks.unsaved,undefined);
  h=harness({version:1,kids:[],chores:[],checks:{restored:true}});await h.ctx.boot();assert.equal(h.ctx.state.checks.restored,true);assert.equal(h.shared.revision,3);
  console.log('Passed shared initialization, queued saves, polling, edit protection, conflict handling, and existing-data load.');
})().catch(e=>{console.error(e);process.exitCode=1});
