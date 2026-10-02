"""Gradio component and callback integration; no browser or live API claimed."""
import asyncio
import json
import xml.etree.ElementTree as ET
from types import SimpleNamespace
import gradio as gr
import pytest
from gradio.state_holder import SessionState
from modules.agent_ui import AgentPanel, ArtifactPanel, browser_form
from test_agent_model import env, select, send, complete, request


def artifact_rows(markup):
    return [[cell.text or '' for cell in row] for row in ET.fromstring(markup).findall('tbody/tr')]


def panel_app(model):
    with gr.Blocks(analytics_enabled=False) as app:
        current=gr.State()
        chatbot=gr.Chatbot()
        status=gr.Markdown()
        panel=AgentPanel();panel.selectors();panel.output_components();panel.settings_components();panel.wire(current,chatbot,status)
    state=SessionState(app);state[current._id]=model
    return app,panel,state


def test_actual_component_output_shapes_and_no_input_file_bubbles(env,monkeypatch):
    complete(env,monkeypatch,True);model=select(env);send(env,model)
    app,panel,state=panel_app(model)
    assert len(panel.values(model))==len(panel.outputs)
    assert len(panel.values(None))==len(panel.outputs)
    assert len(panel.values(model,include_config=False))==len(panel.outputs)
    files=ArtifactPanel.values(model)
    assert len(files[2]['value'])==1 and model.chatbot==[['hello','Agent synthetic answer']]
    assert panel.artifacts.files.interactive is False
    assert isinstance(panel.artifacts.list, gr.HTML)
    assert panel.payload.type=='password' and panel.payload.visible is False
    app.close()


def test_login_form_escapes_external_labels_and_masks_every_field(env):
    model=select(env);model._pending_actions=[{'request_id':'request_one','turn_id':'turn_one','request':{
        'type':'browser_authentication','credential_origin':'https://example.com','reason':'<script>unsafe()</script>',
        'fields':[{'id':'email','type':'text','label':'<img src=x onerror=bad()>','required':True},{'id':'password','type':'password','label':'Password','required':True}],
        'options':[{'id':'password_method','label':'Password','field_ids':['email','password']}]}}]
    form,*buttons=browser_form(model,'request_one')
    assert '<script>' not in form and '<img src=' not in form and '&lt;script&gt;' in form
    assert form.count('type="password"')==2 and 'value="secret' not in form
    assert buttons[-1]['visible'] is True


def test_file_status_markup_escapes_names_types_and_errors(env):
    model=select(env)
    model._artifacts=[{'id':'one','name':'<img src=x onerror="bad()">&.txt',
                       'type':'<script>bad()</script>','status':'failed',
                       'error':'<svg onload="bad()"> & error','size':8}]
    markup=ArtifactPanel.values(model)[1]['value']
    assert '<img' not in markup and '<script' not in markup and '<svg' not in markup
    rows=artifact_rows(markup)
    assert rows[0][0]==model._artifacts[0]['name']
    assert rows[0][1]==model._artifacts[0]['type']
    assert rows[0][3]=='下载失败：'+model._artifacts[0]['error']


def test_actual_gradio_diff_stream_removes_provisional_reference_rows(env,monkeypatch):
    from copy import deepcopy
    from pathlib import Path
    import shutil
    import subprocess
    from modules.model_capabilities import CapabilityUI
    ordinary=select(env,name='GPT3.5 Turbo')
    send(env,ordinary,'old one');send(env,ordinary,'old two')
    model=select(env,ordinary)
    user={'id':'u','type':'message','role':'user','content':[{'type':'input_text','text':'reference and new request'}]}
    answer={'id':'a','type':'message','role':'assistant','content':[{'type':'output_text','text':'completed answer'}]}
    def worker(command):
        if command['action']=='run':
            assert len(command['history_reference'])==4
            yield {'type':'progress','session_id':'s','turn_id':'t','outcome':'in_progress','history_authoritative':True,'items':[user]}
            yield {'type':'result','session_id':'s','turn_id':'t','outcome':'completed','sync_complete':True,'items':[user,answer]}
        elif command['action']=='download':yield {'type':'result','artifacts':[]}
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    with gr.Blocks(analytics_enabled=False) as app:
        current=gr.State();prompt=gr.Textbox();chat=gr.Chatbot();status=gr.Markdown();selector=gr.Dropdown();marker=gr.HTML();button=gr.Button()
        caps=CapabilityUI([],selector,marker)
        panel=AgentPanel();panel.selectors();panel.output_components();panel.settings_components()
        button.click(panel.wrap_predict(env.wrappers['predict'],caps),[current,prompt,chat],[chat,status,*panel.outputs,*caps.outputs])
    state=SessionState(app);state[current._id]=model
    frames=[];expected=[]
    async def exercise():
        iterator=None
        while True:
            result=await app.process_api(0,[None,'new request',ordinary.chatbot],state=state,
                request=gr.Request(session_hash='real-stream'),session_hash='real-stream',iterator=iterator)
            frames.append({'generating':result['is_generating'],'data':deepcopy(result['data'])})
            expected.append(deepcopy(model._display))
            if not result['is_generating']:break
            iterator=result['iterator']
    try:asyncio.run(exercise())
    finally:app.close()
    # Execute the installed Gradio browser client's actual splice-based decoder.
    client_source=None
    for path in (Path(gr.__file__).parent/'templates/frontend/assets').glob('index-*.js.map'):
        source_map=json.loads(path.read_text())
        for source in source_map.get('sourcesContent',[]):
            if source and 'function apply_diff_stream(' in source:
                client_source=source[source.index('function apply_diff_stream('):source.index('function submit(',source.index('function apply_diff_stream('))]
                break
        if client_source:break
    assert client_source,'Installed Gradio client source map is required for the streaming regression'
    script=client_source+'''
const frames=JSON.parse(require('fs').readFileSync(0,'utf8'));
const pending={};const visible=[];let chat=[];
for(const frame of frames){
  const data={data:frame.data};
  if(frame.generating)apply_diff_stream(pending,'event',data);
  const value=data.data[0];
  if(Array.isArray(value))chat=value;
  else if(value && Object.hasOwn(value,'value'))chat=value.value;
  visible.push(JSON.parse(JSON.stringify(chat)));
}
process.stdout.write(JSON.stringify(visible));
'''
    result=subprocess.run([shutil.which('node') or 'node','-e',script],input=json.dumps(frames),text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==expected
    assert expected[-1]==[['reference and new request','completed answer']]


def test_no_login_submit_without_known_origin(env):
    model=select(env);model._pending_actions=[{'request_id':'request_one','request':{'type':'browser_authentication','fields':[]}}]
    form,*buttons=browser_form(model)
    assert buttons[-1]['visible'] is False


def test_gradio_callback_updates_same_session_and_restores_on_failure(env,monkeypatch):
    complete(env,monkeypatch);model=select(env);send(env,model)
    app,panel,state=panel_app(model)
    index=next(i for i,fn in enumerate(app.fns) if fn.fn and fn.fn.__name__=='apply')
    result=asyncio.run(app.process_api(index,[None,'gpt-6-sol','high'],state=state,request=gr.Request(session_hash='ui-test')))
    assert '下一轮' in result['data'][0] and model.model_name=='gpt-6-sol' and model._state['session_id']=='sess_test'
    monkeypatch.setattr(env.agents,'worker_messages',lambda command:iter([{'type':'error','message':'rejected'}]))
    result=asyncio.run(app.process_api(index,[None,'bad','low'],state=state,request=gr.Request(session_hash='ui-test')))
    assert 'rejected' in result['data'][0] and model.model_name=='gpt-6-sol'
    app.close()


def test_gradio_login_callback_clears_payload_and_no_saved_secret(env,monkeypatch):
    model=select(env);model._state={'session_id':'sess_one','turn_id':'turn_one','outcome':'requires_action'}
    model._pending_actions=[{'request_id':'request_one','turn_id':'turn_one','request':{'type':'browser_authentication','credential_origin':'https://example.com','fields':[{'id':'email','label':'Email','required':True,'type':'text'}]}}]
    commands=[]
    def worker(command):
        commands.append(command)
        assert command['action']=='browser_response'
        yield {'type':'result','accepted':True}
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    app,panel,state=panel_app(model)
    index=next(i for i,fn in enumerate(app.fns) if fn.fn and fn.fn.__name__=='login')
    payload=json.dumps({'type':'browser_authentication','action':'submit','fields':[{'field_id':'email','value':'private@example.com'}]})
    result=asyncio.run(app.process_api(index,[None,'request_one',payload],state=state,request=gr.Request(session_hash='ui-test')))
    assert result['data'][0]=='' and model._pending_actions==[]
    assert 'private@example.com' not in repr(model.history) and 'private@example.com' not in repr(model._state)
    assert 'response' not in commands[0]  # The same command dict is cleared after submission.
    app.close()


def test_live_updates_do_not_overwrite_unsaved_tool_form(env):
    model=select(env)
    app,panel,state=panel_app(model)
    values=panel.values(model,include_config=False)
    for component in panel.config_inputs:
        assert values[panel.outputs.index(component)]==gr.update()
    app.close()


def test_callback_owner_validation_is_gradio_injected(env,monkeypatch):
    complete(env,monkeypatch);model=select(env,username='alice')
    app,panel,state=panel_app(model)
    index=next(i for i,fn in enumerate(app.fns) if fn.fn and fn.fn.__name__=='apply')
    with pytest.raises(gr.Error):
        asyncio.run(app.process_api(index,[None,'gpt-6-sol','high'],state=state,request=gr.Request(username='bob',session_hash='ui-test')))
    app.close()


def test_reconnect_waiting_permission_exposes_same_main_stop(env,monkeypatch):
    from modules.model_capabilities import CapabilityUI
    model=select(env);model._state={'session_id':'sess_test','turn_id':'turn_one','generation':'g','outcome':'incomplete'}
    model._session_settings=model._current_settings()
    calls=[]
    def worker(command):
        calls.append(command['action'])
        if command['action']=='recover':
            yield {'type':'progress','session_id':'sess_test','turn_id':'turn_one','outcome':'requires_action','required_actions':[{'request_id':'req','turn_id':'turn_one','request':{'type':'browser_origin_access','origin':'https://example.com'}}]}
            yield {'type':'result','session_id':'sess_test','turn_id':'turn_one','outcome':'cancelled','required_actions':[]}
        elif command['action']=='cancel':yield {'type':'result','outcome':'cancel_requested'}
        elif command['action']=='download':yield {'type':'result','artifacts':[]}
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    with gr.Blocks(analytics_enabled=False) as app:
        current=gr.State();chat=gr.Chatbot();status=gr.Markdown();main_send=gr.Button();main_stop=gr.Button(visible=False);selector=gr.Dropdown();marker=gr.HTML()
        caps=CapabilityUI([],selector,marker,main_send,main_stop);caps.wire(current,chat)
        panel=AgentPanel();panel.selectors();panel.output_components();panel.settings_components();panel.wire(current,chat,status,caps)
        main_stop.click(env.wrappers['interrupt'],[current],[status],queue=False)
    state=SessionState(app);state[current._id]=model
    indices={fn.fn.__name__:i for i,fn in enumerate(app.fns) if fn.fn}
    async def exercise():
        req=gr.Request(session_hash='ui')
        result=await app.process_api(indices['reconnect'],[None],state=state,request=req)
        assert result['data'][2+caps.outputs.index(main_stop)]['visible']
        result=await app.process_api(indices['reconnect'],[None],state=state,request=req,iterator=result['iterator'])
        assert result['data'][2+caps.outputs.index(main_stop)]['visible'] and model._pending_actions
        await app.process_api(indices['interrupt'],[None],state=state,request=req)
        assert 'cancel' in calls
        results=[]
        while result['is_generating']:
            result=await app.process_api(indices['reconnect'],[None],state=state,request=req,iterator=result['iterator'])
            results.append(result)
        updates=[entry['data'][2+caps.outputs.index(main_stop)] for entry in results if isinstance(entry['data'][2+caps.outputs.index(main_stop)],dict)]
        assert any(update.get('visible') is False for update in updates)
    try:asyncio.run(exercise())
    finally:app.close()


def test_predict_ui_stream_exposes_preparing_then_individual_files(env,monkeypatch):
    from pathlib import Path
    import tempfile
    from modules.model_capabilities import CapabilityUI
    model=select(env)
    path=Path(tempfile.mkdtemp(prefix='chuanhu-agent-artifacts-'))/'one.txt';path.write_text('1')
    def worker(command):
        if command['action']=='run':yield {'type':'result','session_id':'sess_test','turn_id':'turn_one','outcome':'completed','text':''}
        elif command['action']=='download':
            records=[{'id':'a','name':'one.txt','type':'text/plain','size':1,'status':'preparing'},{'id':'b','name':'two.txt','type':'text/plain','size':2,'status':'preparing'}]
            yield {'type':'progress','artifacts':records}
            records=[dict(records[0],status='ready',path=str(path)),records[1]]
            yield {'type':'progress','artifacts':records}
            yield {'type':'result','artifacts':[records[0],dict(records[1],status='failed',error='unavailable')]}
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    with gr.Blocks(analytics_enabled=False) as app:
        current=gr.State();prompt=gr.Textbox();chat=gr.Chatbot();status=gr.Markdown();send_button=gr.Button();stop=gr.Button();selector=gr.Dropdown();marker=gr.HTML()
        caps=CapabilityUI([],selector,marker,send_button,stop);caps.wire(current,chat)
        panel=AgentPanel();panel.selectors();panel.output_components();panel.settings_components()
        send_button.click(panel.wrap_predict(env.wrappers['predict'],caps),[current,prompt,chat],[chat,status,*panel.outputs,*caps.outputs])
    state=SessionState(app);state[current._id]=model
    index=next(i for i,fn in enumerate(app.fns) if fn.fn and fn.fn.__name__=='predict_with_ui')
    async def exercise():
        req=gr.Request(session_hash='ui');result=await app.process_api(index,[None,'files',[]],state=state,request=req);rows=[]
        while True:
            update=result['data'][2+panel.outputs.index(panel.artifacts.list)]
            if isinstance(update,dict) and update.get('value'):rows.append(artifact_rows(update['value']))
            if not result['is_generating']:break
            result=await app.process_api(index,[None,'files',[]],state=state,request=req,iterator=result['iterator'])
        assert any(len(r)==2 and all(row[3]=='准备中' for row in r) for r in rows)
        assert any(len(r)==2 and r[0][3]=='可下载' and r[1][3]=='准备中' for r in rows)
    try:asyncio.run(exercise())
    finally:app.close()


def test_completed_wrapped_send_explicitly_unlocks_agent_selectors(env,monkeypatch):
    from modules.model_capabilities import CapabilityUI
    complete(env,monkeypatch);model=select(env)
    with gr.Blocks(analytics_enabled=False) as app:
        current=gr.State();chat=gr.Chatbot();status=gr.Markdown();selector=gr.Dropdown();marker=gr.HTML()
        caps=CapabilityUI([],selector,marker);caps.wire(current,chat)
        panel=AgentPanel();panel.selectors();panel.output_components();panel.settings_components()
    updates=list(panel.wrap_predict(env.wrappers['predict'],caps)(model,'hello',[],request=gr.Request(session_hash='ui')))
    final=updates[-1]
    for component in (panel.model,panel.reasoning,panel.apply_model):
        assert final[2+panel.outputs.index(component)]['interactive'] is True
    assert not model._running
    app.close()


def test_prompt_change_reports_new_session_requirement_in_visible_status(env,monkeypatch):
    complete(env,monkeypatch);model=select(env);send(env,model)
    with gr.Blocks(analytics_enabled=False) as app:
        current=gr.State();prompt=gr.Textbox();status=gr.Markdown()
        prompt.change(env.wrappers['set_system_prompt'],[current,prompt],[status])
    state=SessionState(app);state[current._id]=model
    result=asyncio.run(app.process_api(0,[None,'new instructions'],state=state,request=gr.Request(session_hash='ui')))
    assert '变更需要新会话' in result['data'][0] and '仍使用原设置' in result['data'][0]
    app.close()


@pytest.mark.parametrize('final_status',['ready','failed'])
def test_single_file_retry_streams_preparing_and_result_without_losing_other_files(env,monkeypatch,final_status):
    from pathlib import Path
    import tempfile
    model=select(env);model._state={'session_id':'sess_test','turn_id':'turn_one','generation':'g','outcome':'completed'}
    path=Path(tempfile.mkdtemp(prefix='chuanhu-agent-artifacts-'))/'retry.txt';path.write_text('x')
    existing={'id':'other','name':'other.txt','status':'ready','path':str(path),'size':1,'type':'text/plain'}
    model._artifacts=[existing,{'id':'retry','name':'retry.txt','status':'failed','error':'unavailable','type':'text/plain'}]
    calls=[]
    def worker(command):
        calls.append(command)
        assert command['action']=='download' and command['artifact_ids']==['retry']
        record={'id':'retry','name':'retry.txt','status':'preparing','type':'text/plain','size':1}
        yield {'type':'progress','artifacts':[record]}
        final=dict(record,status=final_status)
        if final_status=='ready':final['path']=str(path)
        else:final['error']='still unavailable'
        yield {'type':'result','artifacts':[final]}
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    app,panel,state=panel_app(model)
    index=next(i for i,fn in enumerate(app.fns) if fn.fn and fn.fn.__name__=='retry_file')
    async def exercise():
        req=gr.Request(session_hash='ui');result=await app.process_api(index,[None,'retry'],state=state,request=req);statuses=[]
        while True:
            update=result['data'][1+panel.outputs.index(panel.artifacts.list)]
            if isinstance(update,dict) and update.get('value'):
                rows=artifact_rows(update['value']);assert rows[0][0]=='other.txt' and rows[0][3]=='可下载';statuses.append(rows[1][3])
            if not result['is_generating']:break
            result=await app.process_api(index,[None,'retry'],state=state,request=req,iterator=result['iterator'])
        assert '准备中' in statuses
        assert any(status.startswith('可下载' if final_status=='ready' else '下载失败') for status in statuses)
    try:asyncio.run(exercise())
    finally:app.close()
    assert len(calls)==1 and model._state['session_id']=='sess_test'
