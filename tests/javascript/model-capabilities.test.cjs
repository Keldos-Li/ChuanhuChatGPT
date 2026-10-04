const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
function element() { return {hidden:false, disabled:false, matches:()=>false}; }
const regenerate=element(), deletion=element(), upload=element(), knowledge=element(), more=element();
const copy=element(),markdown=element();
const single=element(),search=element();single.parent=element();search.parent=element();
for (const cb of [single,search]) {cb.matches=()=>true;cb.closest=()=>cb.parent;}
const marker={dataset:{modelCapabilities:JSON.stringify({regenerate:false,history_delete:false,input_attachments:false,knowledge:false,single_turn:false,external_websearch:false})}};
const selectors={'.regenerate-btn':[regenerate],'.delete-latest-btn':[deletion],'.copy-bot-btn':[copy],'.toggle-md-btn':[markdown],'#upload-files-btn':[upload],'#uploaded-files-btn':[knowledge],'input[name="single-session-cb"]':[single],'input[name="online-search-cb"]':[search]};
let observer;
const eventHandlers = {};
const document={addEventListener:(type,fn)=>{eventHandlers[type]=fn;},readyState:'complete',documentElement:{},querySelector:s=>s.startsWith('#model-capability-state')?marker:s==='#chatbot-input-more-btn-div'?more:null,querySelectorAll:s=>selectors[s]||[]};
const context={document,window:{},queueMicrotask:fn=>fn(),MutationObserver:class {constructor(callback){observer=callback;}observe(){}}};
vm.runInNewContext(fs.readFileSync('web_assets/javascript/model-capabilities.js','utf8'),context);
for (const node of [regenerate,deletion,upload,knowledge,more,single.parent,search.parent]) assert(node.hidden);
assert.strictEqual(context.window.chuanhuSupports('regenerate'),false);
assert(!copy.hidden && !markdown.hidden);
// A late-rendered message must inherit the current capability state.
const late=element();selectors['.regenerate-btn'].push(late);observer();assert(late.hidden);
marker.dataset.modelCapabilities=JSON.stringify({regenerate:true,history_delete:true,input_attachments:true,knowledge:true,single_turn:true,external_websearch:true});observer();
for (const node of [regenerate,deletion,upload,knowledge,more,single.parent,search.parent,late]) assert.strictEqual(node.hidden,false);
for (const node of [regenerate,deletion,upload,knowledge,single,search]) assert.strictEqual(node.disabled,false);
console.log('Capability DOM contract passed: hide, late render, and ordinary restore');

marker.dataset.modelCapabilities=JSON.stringify({history_visit:'current-visit'});observer();
const radio={value:'saved-chat',matches:()=>true};
eventHandlers.pointerdown({target:radio,isTrusted:false});
assert.strictEqual(context.window.chuanhuHistorySelection('saved-chat'),null);
eventHandlers.pointerdown({target:radio,isTrusted:true});
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.window.chuanhuHistorySelection('saved-chat'))),{filename:'saved-chat',visit:'current-visit'});
assert.strictEqual(context.window.chuanhuHistorySelection('saved-chat'),null);
console.log('History intent gate passed: programmatic ignored, trusted visit consumed once');

// Atomic reset response may update the DOM before observer copies snapshot.
marker.dataset.modelCapabilities=JSON.stringify({history_visit:'new-visit-before-observer'});
eventHandlers.pointerdown({target:radio,isTrusted:true});
assert.strictEqual(context.window.chuanhuHistorySelection('saved-chat').visit,'new-visit-before-observer');
console.log('Atomic history visit passed: trusted click reads live response before observer');

let submits=0;
const baseQuery=document.querySelector;
document.querySelector=s=>s.includes('history-intent-submit')?{click:()=>submits++}:baseQuery(s);
context.window.chuanhuClearHistoryIntent();
marker.dataset.modelCapabilities=JSON.stringify({history_visit:'visit-D',history_filename:'saved-chat'});observer();
eventHandlers.pointerdown({type:'pointerdown',button:0,target:radio,isTrusted:true});
assert.strictEqual(submits,1);
// Same response cannot acknowledge a choice still queued behind another selection.
observer();
assert.strictEqual(submits,1);
marker.dataset.modelCapabilities=JSON.stringify({history_visit:'visit-C',history_filename:'other-chat'});observer();
assert.strictEqual(submits,2);
observer();assert.strictEqual(submits,2);
marker.dataset.modelCapabilities=JSON.stringify({history_visit:'visit-D2',history_filename:'saved-chat'});observer();
assert.strictEqual(context.window.chuanhuHistorySelection(),null);
marker.dataset.modelCapabilities=JSON.stringify({history_visit:'visit-new',history_filename:'draft'});observer();
assert.strictEqual(submits,2);
for(const target of [{...radio,disabled:true},{...radio,closest:()=>({})}]) {
 eventHandlers.pointerdown({type:'pointerdown',button:0,target,isTrusted:true});
}
eventHandlers.pointerdown({type:'pointerdown',button:2,target:radio,isTrusted:true});
assert.strictEqual(submits,2);
eventHandlers.pointerdown({type:'pointerdown',button:0,target:radio,isTrusted:true});
context.window.chuanhuClearHistoryIntent();
marker.dataset.modelCapabilities=JSON.stringify({history_visit:'visit-cleared',history_filename:'draft'});observer();
assert.strictEqual(submits,3);
console.log('Latest history intent passed: queued acknowledgment, bounded resubmit, nested actions, clear');
