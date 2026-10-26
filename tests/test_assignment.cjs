const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('dist/index.html','utf8'),src=html.split('<script>')[1].split('</script>')[0];
new vm.Script(src);
const ctx={state:{kids:[{id:'a',age:9},{id:'b',age:11},{id:'c',age:13}],chores:[],checks:{}},week:'2026-10-26',DAY:86400000,schedules:{daily:[0,1,2,3,4,5,6],weekly:[7],monthly:[8],quarterly:[9],mon:[0]},};
vm.createContext(ctx);vm.runInContext(src.slice(src.indexOf('function monday('),src.indexOf('const defaults='))+src.slice(src.indexOf('function offset('),src.indexOf('\nfunction colorTheme(')),ctx);
const chore=(id,minutes,min=9,assignment='rotate',when='weekly')=>({id,name:id,minutes,min,assignment,when,day:null});
function run(chores){ctx.state.chores=chores;return vm.runInContext('assignments()',ctx)}
const totals=r=>Array.from(r.result,list=>list.reduce((sum,c)=>sum+c.assignedDays.length*c.minutes,0));
let r=run([chore('long',30,9,'rotate','mon'),...Array.from({length:6},(_,i)=>chore('short'+i,10,9,'rotate','mon'))]);assert.deepEqual(totals(r),[30,30,30]);
r=run([chore('older',40,13,'rotate','mon'),chore('medium-a',20,9,'rotate','mon'),chore('medium-b',20,9,'rotate','mon'),chore('small-a',20,9,'rotate','mon'),chore('small-b',20,9,'rotate','mon')]);assert.deepEqual(totals(r),[40,40,40]);assert(r.result[2].some(c=>c.id==='older'));
r=run([chore('fixed',30,99,'child:a','mon'),chore('rest-b',30,9,'rotate','mon'),chore('rest-c',30,9,'rotate','mon')]);assert.deepEqual(totals(r),[30,30,30]);assert(r.result[0].some(c=>c.id==='fixed'));
r=run([chore('everyone',20,11,'everyone','mon'),chore('catchup',20,9,'rotate','mon')]);assert.deepEqual(totals(r),[20,20,20]);assert(!r.result[0].some(c=>c.id==='everyone'));
r=run([chore('daily',15,11,'rotate','daily')]);assert.equal(totals(r).reduce((a,b)=>a+b,0),105);assert.equal(r.result[0].length,0);
ctx.state.chores=[chore('month',120,9,'rotate','monthly'),chore('quarter',10,9,'rotate','quarterly')];const plan=ctx.automaticPeriodWeeks();ctx.week=plan.get('month|2026-10');r=vm.runInContext('assignments()',ctx);assert.equal(r.result.flat().filter(c=>c.id==='month').length,1);assert.equal(totals(r).reduce((a,b)=>a+b,0),r.result.flat().reduce((a,c)=>a+c.minutes,0));
// Daily balance takes priority over a lower weekly total.
ctx.week='2026-10-26';ctx.state.kids=[{id:'a',age:9},{id:'b',age:11}];ctx.schedules.tue=[1];
r=run([chore('a-monday',20,9,'child:a','mon'),chore('b-tuesday',60,9,'child:b','tue'),chore('monday-rotating',10,9,'rotate','mon'),chore('flexible',30)]);
assert(r.result[1].some(c=>c.id==='monday-rotating'));
assert(r.result[1].some(c=>c.id==='flexible'));
assert.deepEqual(totals(r),[20,100]);
// With identical daily loads, rotation wins even for the child with more weekly work.
r=run([chore('b-tuesday',60,9,'child:b','tue'),chore('monday-rotating',10,9,'rotate','mon')]);
assert(r.result[1].some(c=>c.id==='monday-rotating'));
const beforeWeeklyChange=r.result.findIndex(list=>list.some(c=>c.id==='monday-rotating'));
ctx.state.chores[0].minutes=1;
assert.equal(vm.runInContext('assignments()',ctx).result.findIndex(list=>list.some(c=>c.id==='monday-rotating')),beforeWeeklyChange);
// Everyone chores contribute to the day's load, but only for eligible children.
r=run([chore('older-monday',20,11,'everyone','mon'),chore('monday-rotating',20,9,'rotate','mon')]);
assert(r.result[0].some(c=>c.id==='monday-rotating'));
assert(!r.result[0].some(c=>c.id==='older-monday'));
// Day-specific assignments remain eligible, complete, and deterministic.
const before=JSON.stringify(r);assert.equal(JSON.stringify(vm.runInContext('assignments()',ctx)),before);
// Flexible monthly/quarterly chores always use the first Monday of the period.
ctx.state.kids=[{id:'a',age:9},{id:'b',age:11}];ctx.state.chores=[chore('first-month',10,9,'rotate','monthly'),chore('first-quarter',10,9,'rotate','quarterly')];
ctx.week='2026-10-05';let firstPlan=ctx.automaticPeriodWeeks();assert.equal(firstPlan.get('first-month|2026-10'),'2026-10-05');assert.equal(firstPlan.get('first-quarter|2026-Q4'),'2026-10-05');assert.equal(vm.runInContext('assignments()',ctx).result.flat().length,2);
ctx.week='2026-10-12';assert.equal(vm.runInContext('assignments()',ctx).result.flat().length,0);
ctx.week='2026-11-02';assert.equal(ctx.automaticPeriodWeeks().get('first-month|2026-11'),'2026-11-02');assert.equal(vm.runInContext('assignments()',ctx).result.flat().length,1);
ctx.week='2027-01-04';assert.equal(ctx.automaticPeriodWeeks().get('first-quarter|2027-Q1'),'2027-01-04');assert.equal(vm.runInContext('assignments()',ctx).result.flat().length,2);
ctx.week='2026-06-01';assert.equal(ctx.automaticPeriodWeeks().get('first-month|2026-06'),'2026-06-01');
// Explicit weekdays keep the first weekday occurrence, even before the first Monday.
ctx.week='2026-09-28';const explicit={...chore('thu',10,9,'rotate','monthly'),day:3};assert.deepEqual(Array.from(ctx.scheduledDays(explicit)),[3]);
assert.equal(ctx.duration({}),10);
console.log('Passed daily-only balancing, weekly-workload independence, rotation determinism, age rules, fixed and Everyone assignments, flexible work, and period schedules.');
