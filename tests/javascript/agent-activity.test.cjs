// Execute actual elapsed UI script with deterministic clock and owned DOM nodes.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('web_assets/javascript/agent-activity.js','utf8');
function fixture({ready='complete',conversation='chat',nodes=[],details=[]}={}) {
    let now=0,active=conversation,next=0;const intervals=new Map(),observers=[],listeners=new Map();
    const app={querySelectorAll:selector=>selector.endsWith('.agent-history-detail')?details:nodes};
    const document={readyState:ready,documentElement:{},addEventListener:(event,fn)=>listeners.set(event,fn),removeEventListener:(event,fn)=>{if(listeners.get(event)===fn)listeners.delete(event);}};
    const context={document,gradioApp:()=>app,performance:{now:()=>now},chuanhuInputConversation:()=>active,
        setInterval:fn=>{intervals.set(++next,fn);return next;},clearInterval:key=>intervals.delete(key),
        MutationObserver:class {constructor(fn){this.fn=fn;observers.push(this);}observe(target,options){this.observing=true;this.options=options;}takeRecords(){const records=this.pending||[];this.pending=[];return records;}disconnect(){this.observing=false;}}};
    vm.createContext(context);const start=()=>vm.runInContext(source,context);
    const notify=(records=[])=>observers.filter(observer=>observer.observing).forEach(observer=>observer.fn(records));
    return {context,start,notify,intervals,listeners,observers,setTime:value=>{now=value;},setConversation:value=>{active=value;},tick:()=>Array.from(intervals.values()).forEach(fn=>fn())};
}
function node(base=1000,running=true,conversation='chat',clone=false){
    const detail={dataset:{conversationId:conversation,layer:'group'},closest:selector=>selector==='.history-message'&&clone?{}:null};
    let text=Math.floor(base/1000)+'s',writes=0;
    return {dataset:{elapsedMs:String(base),running:String(running)},get textContent(){return text;},set textContent(value){text=value;writes++;},get writes(){return writes;},closest:()=>detail};
}
const live=node(),f=fixture({nodes:[live]});f.start();assert.equal(f.intervals.size,1);
f.setTime(500);f.tick();assert.equal(live.textContent,'1s');f.notify();f.setTime(1000);f.tick();assert.equal(live.textContent,'2s','DOM mutations do not restart clocks');
live.dataset.elapsedMs='2200';f.notify();assert.equal(live.textContent,'2s');f.setTime(1500);f.tick();assert.equal(live.textContent,'2s','fresh owner observation replaces baseline');
f.start();assert.equal(f.observers.length,1,'same script version does not duplicate observers');f.setTime(1700);f.tick();assert.equal(live.textContent,'2s','same version reload preserves elapsed clock');
live.dataset.running='false';live.textContent='2s';f.notify();f.setTime(2000);f.tick();assert.equal(live.textContent,'2s');assert.equal(f.intervals.size,0,'completed observation stays frozen');
const stale=node(),g=fixture({nodes:[stale]});g.start();g.setConversation('other');g.setTime(1000);g.tick();assert.equal(stale.textContent,'1s');assert.equal(g.intervals.size,0,'navigation stops stale clocks');
for(const value of [-1,NaN,Infinity,31536000001]){const bad=node(value),h=fixture({nodes:[bad]});h.start();assert.equal(h.intervals.size,0);}
const cloned=node(1000,true,'chat',true),foreign=node(1000,true,'other');const h=fixture({nodes:[cloned,foreign]});h.start();assert.equal(h.intervals.size,0);
const deferred=node(),j=fixture({ready:'loading',nodes:[deferred]});j.start();assert.equal(j.intervals.size,0);j.listeners.get('DOMContentLoaded')();assert.equal(j.intervals.size,1);
j.context.__chuanhuActivityCleanup();assert.equal(j.intervals.size,0);assert(!j.observers[0].observing);
console.log('Elapsed activity: running, mutation, owner baseline, reload, completion, stale visit, invalid metadata, cloned history and cleanup passed');

const detail={dataset:{conversationId:'chat',activityKey:'step'},open:false,closest:()=>null,matches:()=>true};
const details=[detail],kept=fixture({details});kept.start();detail.open=true;kept.listeners.get('toggle')({target:detail});
const completed={...detail,open:false};details[0]=completed;
kept.notify();assert.equal(completed.open,true,'expanded tool remains open after completion DOM replacement');
completed.open=false;kept.listeners.get('toggle')({target:completed});details[0]={...completed,open:true};kept.notify();assert.equal(details[0].open,false,'explicit collapsed state survives running default open');
const foreignDetail={...completed,dataset:{conversationId:'other',activityKey:'step'},open:false};details[0]=foreignDetail;kept.notify();assert.equal(foreignDetail.open,false,'other conversation cannot inherit expanded state');
kept.context.__chuanhuActivityCleanup();assert(!kept.listeners.has('toggle'));
console.log('Activity expansion survives completion and explicit collapse, scoped to conversation');
// Both layers retain choices separately; unstable display occurrences do not.
function ownedDetail(key,layer='tool',persist='true',conversation='chat') {
 return {dataset:{conversationId:conversation,scopeId:'scope',turnId:'t1',activityKey:key,layer,persistOpen:persist},open:false,closest:()=>null,matches:()=>true};
}
const group=ownedDetail('boundary','group'),child=ownedDetail('child'),uncertain=ownedDetail('','tool','false');
const layers=[group,child,uncertain],k=fixture({details:layers});k.start();
for(const detail of layers){detail.open=true;k.listeners.get('toggle')({target:detail});}
layers[0]=ownedDetail('boundary','group');layers[1]=ownedDetail('child');layers[2]=ownedDetail('','tool','false');k.notify();
assert(layers[0].open&&layers[1].open);assert(!layers[2].open,'unresolved snapshot never inherits expansion');
const unstableGroup=ownedDetail('boundary','group','false');layers[0]=unstableGroup;layers[1]=ownedDetail('child');k.notify();
assert(!unstableGroup.open&&layers[1].open,'reliable child remains remembered under non-persistent group');
unstableGroup.open=true;k.listeners.get('toggle')({target:unstableGroup});layers[0]=ownedDetail('new-unresolved','group','false');k.notify();assert(!layers[0].open);
layers[0]=ownedDetail('new-boundary','group');layers[1]=ownedDetail('child');k.notify();assert(!layers[0].open&&layers[1].open,'split group does not blindly inherit, child follows its canonical identity');
layers[0]=ownedDetail('boundary','group','true','other');k.setConversation('other');k.notify();assert(!layers[0].open,'A/B choices do not cross conversation');
layers[0]=ownedDetail('boundary','group');k.setConversation('chat');k.notify();assert(layers[0].open,'A restores its own choice');
console.log('Two layers, regroup, unresolved snapshots and A/B expansion isolation passed');

// Native toggle is asynchronous: DOM changes before callback, stream or timer.
for (const layer of ['group','tool']) {
 for (const initial of [false,true]) {
  const old=ownedDetail('race-'+initial,layer);old.open=initial;
  const list=[old],elapsed=node(),race=fixture({details:list,nodes:[elapsed]});race.start();
  old.open=!initial;race.notify();assert.equal(old.open,!initial,'same-node mutation never restores stale cache');
  race.tick();assert.equal(old.open,!initial,'timer never restores stale cache');
  race.listeners.get('toggle')({target:old});
  const replacement=ownedDetail('race-'+initial,layer);replacement.open=initial;list[0]=replacement;
  race.notify();assert.equal(replacement.open,!initial,'replacement restores last DOM intent');
  // Detached predecessor events and later edits must not replace new intent.
  replacement.open=initial;race.notify();old.open=!initial;
  race.listeners.get('toggle')({target:old});race.tick();assert.equal(replacement.open,initial);
  list[0]=ownedDetail('race-'+initial,layer);race.notify();assert.equal(list[0].open,initial,'new choice survives late old event');
 }
 // Replacement before the old toggle/mutation callback still captures old DOM.
 const old=ownedDetail('early-replace',layer),list=[old],race=fixture({details:list});race.start();
 old.open=true;list[0]=ownedDetail('early-replace',layer);race.notify();assert(list[0].open);
 race.listeners.get('toggle')({target:old});assert(list[0].open);
 // A new DOM user choice before its first observer callback wins old cache.
 const newer=ownedDetail('early-replace',layer);list[0]=newer;
 race.notify([{type:'attributes',attributeName:'open',target:newer}]);assert(!newer.open);
 // Same case with timer draining native attribute records ahead of observer.
 const newest=ownedDetail('early-replace',layer);newest.open=true;list[0]=newest;
 race.observers[0].pending=[{type:'attributes',attributeName:'open',target:newest}];race.notify();assert(newest.open);
 // A subsequent stream replacement carries precisely that most recent choice.
 list[0]=ownedDetail('early-replace',layer);race.notify();assert(list[0].open);
}
console.log('Asynchronous outer/inner open/close, timer/mutation, pre-toggle replacement, new intent and stale events passed');

assert.equal(f.observers[0].options.attributes,true);
assert.deepEqual(Array.from(f.observers[0].options.attributeFilter),['open'],'native open mutations must be observed');

// A replacement restores before paint, with only its local transition disabled.
const restored=ownedDetail('restore-motion'),restoreList=[restored],restore=fixture({details:restoreList});
const frames=[],layouts=[];
restore.context.requestAnimationFrame=fn=>frames.push(fn);
restore.context.getComputedStyle=(detail,pseudo)=>{
 layouts.push({detail,pseudo,open:detail.open,restoring:detail.dataset.restoringOpen});
 return {height:'80px'};
};
restore.start();restored.open=true;restore.notify();
const replacement=ownedDetail('restore-motion');restoreList[0]=replacement;restore.notify();
assert(replacement.open);
assert.equal(replacement.dataset.restoringOpen,'true');
assert.equal(layouts.length,1);assert.equal(layouts[0].pseudo,'::details-content');
assert.equal(layouts[0].open,true);assert.equal(layouts[0].restoring,'true');
replacement.open=false;restore.notify();frames.shift()();
assert.equal(replacement.dataset.restoringOpen,undefined);
assert(!replacement.open,'paint cleanup never overwrites a new native choice');
restoreList[0]=ownedDetail('restore-motion');restore.notify();
assert(!restoreList[0].open);assert.equal(frames.length,0,'unchanged open requires no restoration marker');
console.log('Replacement layout resolves before paint; local restore cleanup preserves new native intent');

// Background tabs may not paint while hundreds of stream replacements arrive.
const backgroundList=[ownedDetail('background-restore')],background=fixture({details:backgroundList});
const pendingFrames=new Map();let frameId=0;
background.context.requestAnimationFrame=fn=>{pendingFrames.set(++frameId,fn);return frameId;};
background.context.cancelAnimationFrame=id=>pendingFrames.delete(id);
background.start();backgroundList[0].open=true;background.notify();
for(let i=0;i<100;i++){
 const previous=backgroundList[0];backgroundList[0]=ownedDetail('background-restore');background.notify();
 assert.equal(previous.dataset.restoringOpen,undefined,'replaced nodes retain no restore marker');
 assert(backgroundList[0].open);assert.equal(pendingFrames.size,1,'all replacements share one pending frame');
}
backgroundList[0].open=false;background.notify();
pendingFrames.values().next().value();pendingFrames.clear();
assert(!backgroundList[0].open,'shared paint never overwrites newer user intent');
assert.equal(backgroundList[0].dataset.restoringOpen,undefined);
backgroundList[0].open=true;background.notify();backgroundList[0]=ownedDetail('background-restore');background.notify();
assert.equal(pendingFrames.size,1);
background.context.__chuanhuActivityCleanup();assert.equal(pendingFrames.size,0);
assert.equal(backgroundList[0].dataset.restoringOpen,undefined,'cleanup clears the current marker and cancels paint');
console.log('100 background replacements share one frame; detached markers and cleanup references are released');

// Reliable fractions are visible below one second; intervals stay precise.
const boundary=node(999.75),precise=fixture({nodes:[boundary]});precise.start();
assert.equal(boundary.textContent,'0.9s');assert.equal(boundary.writes,1);
for(const value of [0,0.1,0.2]){precise.setTime(value);precise.notify();precise.tick();}
assert.equal(boundary.writes,1,'unchanged tenth does not rewrite text');
precise.setTime(0.25);precise.tick();assert.equal(boundary.textContent,'1s');assert.equal(boundary.writes,2);
for(const value of [100,500,999]){precise.setTime(value);precise.notify();precise.tick();}
assert.equal(boundary.writes,2,'unchanged integer second does not rewrite text');
boundary.dataset.elapsedMs='1999.9';precise.notify();assert.equal(boundary.textContent,'1s');assert.equal(boundary.writes,2);
precise.setTime(999.1);precise.tick();assert.equal(boundary.textContent,'2s');assert.equal(boundary.writes,3);
for(const [base,expected] of [[0,'0.0s'],[10,'<0.1s'],[100,'0.1s'],[999,'0.9s'],[1000,'1s'],[60000,'1m 0s']]){
 const live=node(base),test=fixture({nodes:[live]});test.start();assert.equal(live.textContent,expected);
 live.dataset.running='false';test.notify();test.setTime(100000);test.tick();
 assert.equal(live.textContent,expected);assert.equal(test.intervals.size,0,'terminal value stays frozen');
}
for(const base of [0,999,999.75,1000,3456.9]){
 const frozen=node(base,false),stop=fixture({nodes:[frozen]});stop.start();stop.setTime(100000);stop.notify();stop.tick();
 assert.equal(frozen.textContent,Math.floor(base/1000)+'s');assert.equal(frozen.writes,0);assert.equal(stop.intervals.size,0);
}
const missing=node();delete missing.dataset.elapsedMs;missing.textContent='unknown';const unknown=fixture({nodes:[missing]});unknown.start();unknown.setTime(2000);unknown.notify();unknown.tick();
assert.equal(missing.textContent,'unknown');assert.equal(unknown.intervals.size,0);
console.log('Subsecond floors, true zero, reliable interval precision, terminal freeze and unknown baseline passed');

// Only the outer group owns the readout, including localized minute rollover.
const minute=node(59999.75),minutes=fixture({nodes:[minute]});
minute.dataset.secondsFormat='{seconds}s';minute.dataset.minutesFormat='{minutes}分{seconds}秒';
minutes.start();assert.equal(minute.textContent,'59s');
minutes.setTime(.25);minutes.tick();assert.equal(minute.textContent,'1分0秒');assert.equal(minute.writes,1);
for(const time of [1,100,900]){minutes.setTime(time);minutes.notify();minutes.tick();}
assert.equal(minute.writes,1,'same displayed second does not rewrite localized time');
minutes.setTime(7000.25);minutes.tick();assert.equal(minute.textContent,'1分7秒');
minute.dataset.running='false';minute.textContent='1分7秒';minutes.notify();
minutes.setTime(100000);minutes.tick();assert.equal(minute.textContent,'1分7秒');assert.equal(minutes.intervals.size,0);
const childTimer=node(),childDetail=childTimer.closest();childDetail.dataset.layer='tool';
const childFixture=fixture({nodes:[childTimer]});childFixture.start();childFixture.setTime(9000);childFixture.notify();
assert.equal(childTimer.textContent,'1s');assert.equal(childFixture.intervals.size,0,'a child tool cannot run a header timer');
for(const [format,expected] of [['{minutes}m {seconds}s','1m 7s'],['{minutes}분 {seconds}초','1분 7초'],['{minutes} мин {seconds} с','1 мин 7 с']]){
 const localized=node(67999.75),test=fixture({nodes:[localized]});localized.dataset.minutesFormat=format;test.start();
 assert.equal(localized.textContent,expected);
}
console.log('Outer group only, localized 60s rollover, same-second suppression and terminal minute freeze passed');
