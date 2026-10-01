"""Offline acceptance through actual factory, wrappers and BaseLLMModel."""
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import json
import tempfile
import threading
import pytest
import gradio as gr
from offline_models import install

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def env(tmp_path, monkeypatch):
    env = install(ROOT, tmp_path/'history')
    env.agents.shared.chuanhu_path = str(tmp_path)
    env.agents._bindings.clear()
    monkeypatch.setattr(env.agents, 'worker_messages', lambda command: (_ for _ in ()).throw(AssertionError('Unmocked worker')))
    return env

def request(name='browser-one'):
    return SimpleNamespace(username=None, session_hash=name)

def select(env, original=None, browser='browser-one', name='OpenAI Agent'):
    return env.factory.change_model(name,None,'ordinary-key',None,None,'ordinary prompt','',original,request(browser))[0]

def complete(env, monkeypatch, artifacts=False):
    calls=[]
    folder=Path(tempfile.mkdtemp(prefix='chuanhu-agent-artifacts-'))
    file=folder/'synthetic.txt'; file.write_text('Synthetic Agent artifact')
    def worker(command):
        calls.append(deepcopy(command))
        if command['action']=='run':
            yield dict(type='progress',session_id='sess_test',turn_id='turn_'+str(len(calls)),outcome='in_progress',progress='sandbox.running')
            yield dict(type='result',session_id='sess_test',outcome='completed',text='Agent synthetic answer')
        elif command['action']=='download':
            yield dict(type='result',files=[str(file)] if artifacts else [])
        elif command['action']=='inspect':
            yield dict(type='result',session_id='sess_test',outcome='completed',text='Recovered answer')
        else: raise AssertionError(command)
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    return calls,file

def send(env, model, text='hello', chatbot=None, browser='browser-one'):
    return list(env.wrappers['predict'](model,text,model.chatbot if chatbot is None else chatbot,request=request(browser)))

def test_main_factory_send_artifact_followup(env,monkeypatch):
    calls,file=complete(env,monkeypatch,True)
    model=select(env)
    output=send(env,model)
    assert output[-1][0][-1]==[None,(str(file),'synthetic.txt')]
    assert any('sandbox.running' in status for _,status in output)
    send(env,model,'continue')
    runs=[c for c in calls if c['action']=='run']
    assert runs[0]['session_id'] is None and runs[1]['session_id']=='sess_test'
    assert runs[1]['prompt']=='continue' and 'history' not in runs[1]
    assert all(r['api_key'] is None for r in [dict(api_key=model.api_key)])
    assert model.history[-1]['content']=='Agent synthetic answer'
    assert not any('sandbox.running' in x['content'] for x in model.history)

def test_ordinary_agent_switch_and_binding(env,monkeypatch):
    calls,_=complete(env,monkeypatch)
    ordinary=select(env,name='GPT3.5 Turbo')
    send(env,ordinary,'ordinary question')
    agent=select(env,ordinary)
    assert agent.history==ordinary.history and agent.history is not ordinary.history
    assert agent.system_prompt!=ordinary.system_prompt
    send(env,agent,'agent task')
    ordinary2=select(env,agent,name='GPT3.5 Turbo')
    assert ordinary2.system_prompt!=agent.system_prompt
    restored=select(env,ordinary2)
    assert restored._state['session_id']=='sess_test'
    send(env,restored,'owned followup')
    assert [c for c in calls if c['action']=='run'][-1]['session_id']=='sess_test'
    ordinary3=select(env,restored,name='GPT3.5 Turbo')
    send(env,ordinary3,'ordinary new turn')
    fresh=select(env,ordinary3)
    assert fresh._state=={'outcome':'not_started'}

def test_first_yield_close_does_not_send(env):
    model=select(env)
    generator=env.wrappers['predict'](model,'hello',[],request=request())
    next(generator); generator.close()
    assert model.history==[] and model._state['outcome']=='not_started' and not model._running

def test_stop_before_worker_rolls_back(env):
    model=select(env); generator=model.predict('hello',[])
    next(generator); model.interrupt(); list(generator)
    assert model.history==[] and model._state['outcome']=='not_started'

def test_cancel_waits_for_new_turn_not_previous(env,monkeypatch):
    complete(env,monkeypatch); model=select(env); send(env,model)
    calls=[]; ready=threading.Event(); release=threading.Event()
    def worker(command):
        calls.append(deepcopy(command))
        if command['action']=='run':
            ready.set(); assert release.wait(3)
            yield dict(type='progress',session_id='sess_test',turn_id='turn_new',outcome='in_progress')
            yield dict(type='result',session_id='sess_test',turn_id='turn_new',outcome='cancelled')
        elif command['action']=='cancel': yield dict(type='result',outcome='cancel_requested')
        else: raise AssertionError(command)
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    thread=threading.Thread(target=lambda:send(env,model,'second')); thread.start()
    assert ready.wait(3); model.interrupt()
    assert [c['action'] for c in calls]==['run']
    release.set(); thread.join(3)
    assert not thread.is_alive()
    assert [c['action'] for c in calls]==['run','cancel']
    assert model._state['outcome']=='cancelled'
    complete(env,monkeypatch); send(env,model,'after cancellation')

@pytest.mark.parametrize('known',[True,False])
def test_eof_blocks_resubmission_and_retry_is_readonly(env,monkeypatch,known):
    calls=[]
    def worker(command):
        calls.append(deepcopy(command))
        if command['action']=='run' and known:
            yield dict(type='progress',session_id='sess_test',turn_id='turn_one',outcome='in_progress',text='partial')
        elif command['action'] in ('inspect','recover_unknown'):
            yield dict(type='result',session_id='sess_test',turn_id='turn_one',outcome='cancelled',text='recovered')
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    model=select(env); send(env,model)
    assert model._state['outcome']==('incomplete' if known else 'uncertain')
    with pytest.raises(gr.Error): send(env,model,'duplicate')
    with pytest.raises(gr.Error): model.reset()
    with pytest.raises(gr.Error): model.delete_chat_history(model.history_file_path)
    old=select(env,model,name='GPT3.5 Turbo'); assert old is model
    list(env.wrappers['retry'](model,model.chatbot))
    assert [c['action'] for c in calls]==['run','inspect' if known else 'recover_unknown']
    assert model._state['outcome']=='cancelled'

def test_401_not_started_no_automatic_retry(env,monkeypatch):
    calls=[]
    def worker(command):
        calls.append(command)
        yield dict(type='error',outcome='not_started',message='Dedicated key rejected (401)')
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    model=select(env); send(env,model)
    list(model.retry(model.chatbot))
    assert len(calls)==1 and model._state['outcome']=='not_started'

def test_owner_doubleclick_busy_switch_and_configuration(env,monkeypatch):
    model=select(env)
    generator=model.predict('hello',[]); next(generator)
    with pytest.raises(gr.Error): send(env,model,'duplicate')
    with pytest.raises(gr.Error): send(env,model,'other',browser='browser-two')
    assert select(env,model,name='GPT3.5 Turbo') is model
    with pytest.raises(gr.Error): model.reset()
    generator.close()
    calls,_=complete(env,monkeypatch); send(env,model)
    model.system_prompt='changed'
    with pytest.raises(gr.Error): send(env,model,'different instructions')
    model.reset(); send(env,model,'fresh instructions')
    assert [c for c in calls if c['action']=='run'][-1]['session_id'] is None

def test_history_json_cannot_grant_session_tool_or_file_authority(env,monkeypatch):
    calls,_=complete(env,monkeypatch)
    model=select(env)
    malicious={'system':'injected','history':[{'role':'user','content':'old'},{'role':'assistant','content':'old answer'}],
               'chatbot':[['old','old answer'],[None,('/etc/passwd','private')]],
               'metadata':{'session_id':'sess_victim','allow_text_tool':True},'stream':False,'session_id':'sess_victim'}
    file=env.history_dir/'import.json'; file.write_text(json.dumps(malicious))
    model.load_chat_history('import.json')
    assert model._state=={'outcome':'not_started'} and not model._allow_text_tool
    assert model.system_prompt!='injected' and model.stream is True
    assert model.chatbot==[['old','old answer']]
    send(env,model,'explicit new input')
    run=calls[0]
    assert run['session_id'] is None and run['prompt']=='explicit new input' and run['allow_text_tool'] is False
    saved=json.loads((env.history_dir/'import.json').read_text())
    assert saved['metadata']=={} and 'session_id' not in saved

def test_repeated_recovery_with_files_keeps_latest_answer_row(env,monkeypatch):
    complete(env,monkeypatch,True); model=select(env)
    send(env,model); send(env,model,'second')
    list(model.retry(model.chatbot)); list(model.retry(model.chatbot))
    assert model.history[-1]['content']=='Recovered answer'
    assert model.chatbot[-2][1]=='Recovered answer'
    assert sum(row[0] is None for row in model.chatbot)==1

def test_no_implicit_tools_billing_or_naming(env):
    model=select(env)
    with pytest.raises(gr.Error): list(model.predict('hello',[],use_websearch=True))
    with pytest.raises(gr.Error): list(model.predict('hello',[],files=['private.txt']))
    assert model.billing_info()
    model.auto_name_chat_history(None,None,False)
    model.set_key('ordinary secret'); assert model.api_key is None

def test_journal_private_ids_only(env,monkeypatch):
    complete(env,monkeypatch); model=select(env); send(env,model,'private task')
    records=list(Path(model._owner and model.__class__.__module__ and env.agents.shared.chuanhu_path).glob('agent_data/*/*.json'))
    assert len(records)==1
    assert records[0].stat().st_mode & 0o777 == 0o600
    text=records[0].read_text(); assert 'private task' not in text and 'Agent synthetic answer' not in text
    assert json.loads(text)['session_id']=='sess_test'

def test_gradio_request_injection(env):
    from gradio.helpers import special_args
    browser=gr.Request(session_hash='native-browser')
    args,_,_=special_args(env.wrappers['predict'],[None,'text',[],False,None,'English'],request=browser)
    assert args[-1] is browser

def test_upload_identical_transcript_cannot_clone_remote_session(env,monkeypatch):
    calls,_=complete(env,monkeypatch);model=select(env);send(env,model)
    serialized=(env.history_dir/model.history_file_path).read_bytes()
    model.upload_chat_history(serialized)
    assert model._state=={'outcome':'not_started'}
    send(env,model,'new explicit task')
    assert [c for c in calls if c['action']=='run'][-1]['session_id'] is None


def test_switch_is_atomic_against_old_queued_predict(env,monkeypatch):
    complete(env,monkeypatch);old=select(env);send(env,old)
    import sys
    ordinary=sys.modules['modules.models.OpenAIVision']
    constructor=ordinary.OpenAIVisionClient
    ready=threading.Event();release=threading.Event();errors=[];switched=[]
    def blocked(*a,**k):
        ready.set();assert release.wait(3);return constructor(*a,**k)
    monkeypatch.setattr(ordinary,'OpenAIVisionClient',blocked)
    switch=threading.Thread(target=lambda:switched.append(select(env,old,name='GPT3.5 Turbo')))
    switch.start();assert ready.wait(3)
    def queued():
        try:send(env,old,'queued old request')
        except gr.Error as error:errors.append(error)
    predict=threading.Thread(target=queued);predict.start();release.set()
    switch.join(3);predict.join(3)
    assert not switch.is_alive() and not predict.is_alive()
    assert errors and switched[0] is not old and old._retired


def test_failed_switch_releases_old_model(env,monkeypatch):
    complete(env,monkeypatch);old=select(env);send(env,old)
    import sys
    def failed(*a,**k):raise RuntimeError('Synthetic constructor failure')
    monkeypatch.setattr(sys.modules['modules.models.OpenAIVision'],'OpenAIVisionClient',failed)
    assert select(env,old,name='GPT3.5 Turbo') is old
    assert not old._retired
    send(env,old,'old model remains usable')


def test_unknown_turn_recovery_then_pending_cancel(env,monkeypatch):
    complete(env,monkeypatch);model=select(env);send(env,model)
    calls=[]
    def worker(command):
        calls.append(deepcopy(command))
        if command['action']=='run':
            yield dict(type='progress',session_id='sess_test',outcome='incomplete',baseline_turn_ids=['old'],submission_started=True)
        elif command['action']=='inspect':
            assert command['turn_id'] is None and command['baseline_turn_ids']==['old']
            yield dict(type='result',session_id='sess_test',turn_id='new',outcome='in_progress')
        elif command['action']=='cancel':yield dict(type='result',outcome='cancel_requested')
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    send(env,model,'new followup');model.interrupt();list(model.retry(model.chatbot))
    assert [c['action'] for c in calls]==['run','inspect','cancel']
    assert model._state['turn_id']=='new' and model._state['outcome']=='cancel_requested'


def test_failed_readonly_recovery_does_not_unlock_uncertain_submission(env,monkeypatch):
    model=select(env)
    monkeypatch.setattr(env.agents,'worker_messages',lambda c:iter([]))
    send(env,model)
    monkeypatch.setattr(env.agents,'worker_messages',lambda c:iter([dict(type='error',outcome='not_started',message='Read failed',baseline_turn_ids=None,submission_started=False)]))
    list(model.retry(model.chatbot))
    assert model._state['outcome']=='uncertain'
    with pytest.raises(gr.Error):send(env,model,'duplicate')

def test_owned_binding_restores_explicit_agent_instructions(env,monkeypatch):
    calls,_=complete(env,monkeypatch);model=select(env)
    model.system_prompt='Explicit Agent instructions';send(env,model)
    ordinary=select(env,model,name='GPT3.5 Turbo')
    restored=select(env,ordinary)
    assert restored.system_prompt=='Explicit Agent instructions'
    send(env,restored,'followup')
    assert [c for c in calls if c['action']=='run'][-1]['session_id']=='sess_test'

def test_followup_preflight_failure_readonly_retry_keeps_not_started(env,monkeypatch):
    complete(env,monkeypatch);model=select(env);send(env,model)
    calls=[]
    def worker(command):
        calls.append(command)
        yield dict(type='error',outcome='not_started',session_id=None,turn_id=None,message='Synthetic missing dedicated key')
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    send(env,model,'unsent followup');list(model.retry(model.chatbot))
    assert len(calls)==1 and model._state['outcome']=='not_started'
    model.reset()

def test_stop_during_download_does_not_cancel_completed_turn(env,monkeypatch):
    model=select(env);calls=[]
    def worker(command):
        calls.append(command)
        if command['action']=='run':
            yield dict(type='result',session_id='sess_test',turn_id='turn_one',outcome='completed',text='done')
        elif command['action']=='download':
            model.interrupt()
            yield dict(type='result',files=[])
        else:raise AssertionError(command)
    monkeypatch.setattr(env.agents,'worker_messages',worker);send(env,model)
    assert model._state['outcome']=='completed'
    assert [c['action'] for c in calls]==['run','download']


def test_ordinary_partial_switch_rejected_then_complete_history_roundtrip(env,monkeypatch):
    complete(env,monkeypatch)
    ordinary=select(env,name='GPT3.5 Turbo')
    def stream():
        yield 'ordinary partial'
        yield 'ordinary completed'
    ordinary.get_answer_stream_iter=stream
    generator=ordinary.predict('ordinary task',[])
    next(generator);next(generator);partial=next(generator)
    assert partial[0][-1][1]=='ordinary partial'
    assert ordinary.history==[{'role':'user','content':'ordinary task'}]
    switched=env.factory.change_model('OpenAI Agent',None,None,None,None,None,'',ordinary,request())
    assert switched[0] is ordinary and switched[8]['value']=='GPT3.5 Turbo'
    assert ordinary._chat_running and not ordinary._chat_retired
    list(generator)
    assert ordinary.history[-1]=={'role':'assistant','content':'ordinary completed'}
    agent=select(env,ordinary);send(env,agent,'agent task')
    expected=[{'role':'user','content':'ordinary task'},{'role':'assistant','content':'ordinary completed'},
              {'role':'user','content':'agent task'},{'role':'assistant','content':'Agent synthetic answer'}]
    assert agent.history==expected
    saved=json.loads((env.history_dir/agent.history_file_path).read_text())
    assert saved['history']==expected
    assert saved['chatbot']==[['ordinary task','ordinary completed'],['agent task','Agent synthetic answer']]
    agent.load_chat_history(agent.history_file_path)
    assert agent.history==expected and agent.chatbot==saved['chatbot']
    imported=select(env,browser='browser-two');imported.load_chat_history(agent.history_file_path)
    assert imported.history==expected and imported._state=={'outcome':'not_started'}


@pytest.mark.parametrize('close_at',[0,2])
def test_closed_ordinary_iterator_restores_complete_history_before_switch(env,monkeypatch,close_at):
    complete(env,monkeypatch);ordinary=select(env,name='GPT3.5 Turbo')
    send(env,ordinary,'completed task');previous=deepcopy(ordinary.history);display=deepcopy(ordinary.chatbot)
    generator=ordinary.predict('interrupted task',deepcopy(display))
    for _ in range(close_at+1):next(generator)
    generator.close()
    assert ordinary.history==previous and ordinary.chatbot==display and not ordinary._chat_running
    agent=select(env,ordinary);assert agent.history==previous
    send(env,agent,'agent task')
    agent.load_chat_history(agent.history_file_path)
    assert agent.history[-1]['content']=='Agent synthetic answer' and len(agent.history)==4


def test_ordinary_switch_atomic_rejects_queued_old_predict(env,monkeypatch):
    ordinary=select(env,name='GPT3.5 Turbo');send(env,ordinary)
    constructor=env.agents.OpenAIAgentsClient
    ready=threading.Event();release=threading.Event();errors=[];switched=[]
    def blocked(*a,**k):
        ready.set();assert release.wait(3);return constructor(*a,**k)
    monkeypatch.setattr(env.agents,'OpenAIAgentsClient',blocked)
    switch=threading.Thread(target=lambda:switched.append(select(env,ordinary)))
    switch.start();assert ready.wait(3)
    def queued():
        try:list(ordinary.predict('late ordinary task',ordinary.chatbot))
        except gr.Error as error:errors.append(error)
    old_predict=threading.Thread(target=queued);old_predict.start();release.set()
    switch.join(3);old_predict.join(3)
    assert not switch.is_alive() and not old_predict.is_alive()
    assert errors and switched[0] is not ordinary and ordinary._chat_retired
    assert switched[0].history==ordinary.history and len(ordinary.history)==2


def test_ordinary_stop_keeps_partial_pair_and_can_switch(env,monkeypatch):
    complete(env,monkeypatch);ordinary=select(env,name='GPT3.5 Turbo')
    def stream():
        yield 'ordinary partial'
        yield 'ordinary completed'
    ordinary.get_answer_stream_iter=stream
    generator=ordinary.predict('ordinary task',[])
    next(generator);next(generator);next(generator)
    ordinary.interrupt();list(generator)
    assert ordinary.history[-1]['content']=='ordinary partial' and not ordinary._chat_running
    agent=select(env,ordinary);send(env,agent,'agent task')
    agent.load_chat_history(agent.history_file_path)
    assert len(agent.history)==4 and agent.history[-1]['content']=='Agent synthetic answer'


def test_ordinary_retry_reserves_before_rewind_and_close_restores_history(env,monkeypatch):
    complete(env,monkeypatch);ordinary=select(env,name='GPT3.5 Turbo');send(env,ordinary)
    previous=deepcopy(ordinary.history)
    generator=ordinary.retry(deepcopy(ordinary.chatbot));next(generator)
    assert ordinary._chat_running
    assert select(env,ordinary) is ordinary
    generator.close()
    assert ordinary.history==previous and not ordinary._chat_running
    ordinary.get_answer_stream_iter=lambda:iter(['ordinary regenerated'])
    list(ordinary.retry(deepcopy(ordinary.chatbot)))
    assert len(ordinary.history)==2 and ordinary.history[-1]['content']=='ordinary regenerated'
