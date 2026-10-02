import asyncio
import gradio as gr
import pytest
from gradio.state_holder import SessionState
from modules.agent_ui import AgentPanel
from test_agent_model import env, request
from test_agent_input_model import staged


@pytest.mark.parametrize('outcome',['failed','cancelled','submitted'])
def test_actual_finish_callback_keeps_unsent_draft_and_feedback(env,tmp_path,monkeypatch,outcome):
    name='upload-fail.txt' if outcome=='failed' else 'slow-upload.txt' if outcome=='cancelled' else 'notes.txt'
    model,paths,service,calls=staged(env,tmp_path,monkeypatch,(name,))
    text='Keep this unsent attachment request'
    panel=AgentPanel()
    transferred=panel.wrap_transfer(env.wrappers['transfer_input'])(text,model,agent_files=paths,request=request())
    assert 'value' not in transferred[1]  # Browser draft is retained until acknowledgement.
    envelope=transferred[0]
    iterator=env.wrappers['predict'](model,envelope,[],files=paths,request=request())
    if outcome=='cancelled':
        next(iterator);next(iterator);next(iterator);model.interrupt()
    list(iterator)
    panel=AgentPanel()
    with gr.Blocks(analytics_enabled=False) as app:
        current=gr.State();question=gr.State();draft=gr.Textbox();status=gr.Markdown();finish=gr.Button()
        finish.click(panel.finish_submission,[current,question,draft],[draft,status])
    state=SessionState(app);state[current._id]=model;state[question._id]=envelope
    async def exercise():
        result=await app.process_api(0,[None,None,''],state=state,request=request())
        if outcome=='submitted': assert 'value' not in result['data'][0]
        else:
            assert 'value' not in result['data'][0]
            assert '消息未发送' in result['data'][1] if outcome=='failed' else '已取消' in result['data'][1]
            assert not model.history and model._pending_upload_paths
        edited=await app.process_api(0,[None,None,'A newer draft'],state=state,request=request())
        assert 'value' not in edited['data'][0]
        model.reset()
        stale_chat=await app.process_api(0,[None,None,''],state=state,request=request())
        assert all('value' not in item for item in stale_chat['data'])
        model._draft_token='new-attempt'
        stale=await app.process_api(0,[None,None,''],state=state,request=request())
        assert all('value' not in item for item in stale['data'])
        with pytest.raises(gr.Error):
            await app.process_api(0,[None,None,''],state=state,request=request(username='other'))
    try:asyncio.run(exercise())
    finally:app.close()


def test_hidden_rag_updates_cannot_clear_agent_status():
    import subprocess
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');let callback,clears=0,knowledge=false;
const uploader={observe(){}};const source=fs.readFileSync('web_assets/javascript/fake-gradio.js','utf8');
const context={window:{chuanhuSupports:()=>knowledge},gradioApp:()=>({querySelector:()=>({innerText:'',addEventListener(){},removeEventListener(){}})}),
 chatbotArea:{classList:{remove(){}}},uploaderIndicator:{},uploaderIndicator2:{},statusDisplayMessage:()=>clears++,MutationObserver:class{constructor(fn){callback=fn;}observe(){}}};
vm.createContext(context);vm.runInContext(source,context);
context.transUpload=()=>{};context.gradioApp=()=>({querySelector:s=>s.includes('table.file-preview')?null:{innerText:''}});
context.setUploader();callback();assert.strictEqual(clears,0);knowledge=true;callback();assert.strictEqual(clears,1);
'''
    result=subprocess.run(['node','-e',script],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_browser_only_clears_the_unchanged_acknowledged_draft():
    import subprocess
    result=subprocess.run(['node','tests/javascript/agent-draft-guard.test.cjs'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_starting_input_request_keeps_draft_and_files_until_a_turn_is_seen(env,tmp_path,monkeypatch):
    model,paths,service,calls=staged(env,tmp_path,monkeypatch,('notes.txt',))
    panel=AgentPanel()
    envelope=panel.wrap_transfer(env.wrappers['transfer_input'])('Keep until accepted',model,agent_files=paths,request=request())[0]
    model._pending_send=None
    model._state={'generation':'g','session_id':'session','outcome':'starting','turn_id':None}
    identifiers=[item.input_id for item in model._input_stager.snapshot()]
    model._input_context={'input_ids':identifiers,'run_id':'g'}
    model._accept({'session_id':'session','submission_started':True,'outcome':'starting'},'g')
    assert not model._draft_acknowledged and model._pending_upload_paths==tuple(paths)
    assert model._input_context is not None
    model._accept({'session_id':'session','submission_started':True,'turn_id':'new-turn','outcome':'in_progress'},'g')
    assert model._draft_acknowledged and not model._pending_upload_paths
    assert model._input_context is None
