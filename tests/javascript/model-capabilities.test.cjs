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
const document={readyState:'complete',documentElement:{},querySelector:s=>s.startsWith('#model-capability-state')?marker:s==='#chatbot-input-more-btn-div'?more:null,querySelectorAll:s=>selectors[s]||[]};
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
