"""Real worker/runtime error shapes through the main model, with fake SDK only."""
from contextlib import contextmanager
import importlib.util
import io
import json
import sys
import pytest
from test_agent_model import env, select, send, complete, ROOT
from test_agent_runtime import runtime, FakeClient, turn, text


@pytest.mark.parametrize('failure',['busy','history_limit','missing_key','missing_sdk'])
def test_unsent_worker_failure_can_reset_and_explicitly_submit(env,monkeypatch,failure):
    complete(env,monkeypatch)
    model=select(env);send(env,model,'completed previous turn')
    fake=FakeClient([turn('created'),text('done','new result'),turn('completed')],
                    session_status='in_progress' if failure=='busy' else 'idle')
    if failure=='history_limit':
        fake.beta.agents.sessions.turns.list=lambda *a,**k:[{'id':'old_'+str(n)} for n in range(501)]
    monkeypatch.setitem(sys.modules,'runtime',runtime)
    spec=importlib.util.spec_from_file_location('worker_model_bridge',ROOT/'optional/agents/worker.py')
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    failing=[failure]
    def key():
        if failing[0]=='missing_key':raise runtime.AgentError('Synthetic missing key')
        return 'synthetic-only-key'
    @contextmanager
    def client(key):
        if failing[0]=='missing_sdk':raise runtime.AgentError('Synthetic unavailable SDK')
        yield fake
    monkeypatch.setattr(worker,'read_dedicated_key',key)
    monkeypatch.setattr(worker,'create_client',client)
    commands=[]
    def bridge(command):
        commands.append(command)
        emitted=[]
        monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps(command)+'\n'))
        monkeypatch.setattr(worker,'emit',lambda kind,**data:emitted.append(dict(type=kind,**data)))
        worker.main()
        yield from emitted
    monkeypatch.setattr(env.agents,'worker_messages',bridge)
    send(env,model,'unsent followup')
    assert model._state['outcome']=='not_started'
    assert model._state['submission_started'] is False
    assert fake.submitted==[] and fake.payloads==[]
    list(model.retry(model.chatbot))
    assert len(commands)==1 and model._state['outcome']=='not_started'
    model.reset()
    assert model._state['outcome']=='not_started' and model._state.get('session_id') is None
    failing[0]=None;fake.session_status='idle'
    output=send(env,model,'explicit fresh submission')
    assert model._state['outcome']=='completed'
    assert model.history[-1]['content']=='new result'
    assert len(fake.payloads)==1 and fake.submitted==[]
    assert output[-1][0][-1]==['explicit fresh submission','new result']
