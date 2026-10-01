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
