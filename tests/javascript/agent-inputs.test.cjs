const fs=require('fs'),vm=require('vm'),assert=require('assert');
let target='#agent-upload-files',busy=false,selected=[],events=0;
const input={disabled:false,files:[],dispatchEvent:()=>events++};
class DataTransfer { constructor(){this.files=[];this.items={add:file=>this.files.push(file)};} }
const context={window:{chuanhuSupports:()=>true,chuanhuInputBusy:()=>busy,chuanhuInputTarget:()=>target},
    gradioApp:()=>({querySelector:selector=>{selected.push(selector);return input;}}),DataTransfer,Event:class {}};
vm.createContext(context);vm.runInContext(fs.readFileSync('web_assets/javascript/file-input.js','utf8'),context);
(async()=>{
    await context.upload_files([{name:'one.png'}]);await context.upload_files([{name:'two.png'}]);
    assert.strictEqual(input.files[0].name,'two.png');assert.strictEqual(events,2);
    assert(selected.every(selector=>selector==='#agent-upload-files input[type=file]'));
    busy=true;await context.upload_files([{name:'blocked'}]);assert.strictEqual(events,2);
    busy=false;target='#upload-index-file';await context.upload_files([{name:'ordinary.pdf'}]);
    assert.strictEqual(selected.at(-1),'#upload-index-file input[type=file]');
    let change,mutated,current='original',error=false;
    const document={readyState:'complete',documentElement:{},addEventListener:(name,fn)=>{if(name==='change')change=fn;},
        querySelector:()=>error?{}:null};
    const window={chuanhuInputConversation:()=>current};
    vm.runInNewContext(fs.readFileSync('web_assets/javascript/agent-inputs.js','utf8'),
        {document,window,MutationObserver:class{constructor(fn){mutated=fn;}observe(){}}});
    change({composedPath:()=>[{matches:()=>true,files:[{}]}]});current='another-conversation';
    assert.strictEqual(window.chuanhuAgentUploadTarget,'original');assert(window.chuanhuAgentUploading);
    error=true;mutated();assert.strictEqual(window.chuanhuAgentUploading,false);
    console.log('Input routing, repeated paste, busy guard, captured conversation and upload error passed');
})().catch(error=>{console.error(error);process.exit(1);});
