"""Gradio component and callback integration; no browser or live API claimed."""
import asyncio
import json
from types import SimpleNamespace
import gradio as gr
import pytest
from gradio.state_holder import SessionState
from modules.agent_ui import AgentPanel, ArtifactPanel, browser_form
from test_agent_model import env, select, send, complete, request


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
            if isinstance(update,dict) and isinstance(update.get('value'),dict):rows.append(update['value']['data'])
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
