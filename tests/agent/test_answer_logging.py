import logging
import pytest
from agent_fixtures import env, select, send
from test_runtime import message


@pytest.mark.parametrize('path', ['predict', 'reconnect', 'observe'])
def test_final_answer_logging_waits_for_reconciliation_and_deduplicates(env, monkeypatch, caplog, path):
    model = select(env)
    final = '完整正文\n' + 'long answer ' * 2000
    partial = 'partial'
    def worker(command):
        if command['action'] == 'download':
            yield dict(type='result', artifacts=[])
            return
        assert command['action'] in ('run', 'recover', 'observe')
        yield dict(type='progress', session_id='sess_test', turn_id='t1', outcome='completed', text=partial)
        assert not [r for r in caplog.records if r.msg == '回答为：%s']
        yield dict(type='result', session_id='sess_test', turn_id='t1', outcome='completed',
                   sync_complete=True, items=[message('u1', 'question', role='user'), message('a1', final)])
    monkeypatch.setattr(env.agents, 'worker_messages', worker)
    caplog.set_level(logging.INFO)
    if path == 'predict':
        send(env, model, 'question')
    else:
        model._state = dict(generation='g', session_id='sess_test', turn_id='t1', outcome='incomplete')
        model.history = [{'role': 'user', 'content': 'question'}, {'role': 'assistant', 'content': partial}]
        model._display = model.chatbot = [['question', partial]]
        model._answer_index, model._answer_row = 1, 0
        model._needs_sync = True
        model._session_settings = model._current_settings()
        list(model.reconnect() if path == 'reconnect' else model.observe_history())
    records = [r for r in caplog.records if r.msg == '回答为：%s']
    assert len(records) == 1 and records[0].getMessage() == '回答为：' + final
    # Repeated final snapshots are quiet; a later reconciled extension is retained.
    snapshot = dict(type='result', session_id='sess_test', turn_id='t1', outcome='completed',
                    sync_complete=True, items=[message('u1', 'question', role='user'), message('a1', final)])
    model._accept(snapshot, model._state['generation'], restoring=True)
    assert len([r for r in caplog.records if r.msg == '回答为：%s']) == 1
    snapshot['items'][-1] = message('a1', final + '\nlate completion')
    model._accept(snapshot, model._state['generation'], restoring=True)
    records = [r for r in caplog.records if r.msg == '回答为：%s']
    assert len(records) == 2 and records[-1].getMessage() == '回答为：' + final + '\nlate completion'


@pytest.mark.parametrize('outcome', ['failed', 'cancelled'])
@pytest.mark.parametrize('path', ['predict', 'reconnect', 'observe'])
def test_empty_terminal_turn_never_logs_previous_answer(env, monkeypatch, caplog, outcome, path):
    model = select(env)
    model.history = [{'role': 'user', 'content': 'previous question'}, {'role': 'assistant', 'content': 'previous answer'}]
    model._display = model.chatbot = [['previous question', 'previous answer']]
    previous_items = [message('old-u', 'previous question', 'old', role='user'), message('old-a', 'previous answer', 'old')]
    def worker(command):
        if command['action'] == 'download':
            yield dict(type='result', artifacts=[])
        else:
            yield dict(type='result', session_id='sess_test', turn_id='new', outcome=outcome,
                       sync_complete=True, items=previous_items + [message('new-u', 'new question', 'new', role='user')])
    monkeypatch.setattr(env.agents, 'worker_messages', worker)
    caplog.set_level(logging.INFO)
    if path == 'predict':
        send(env, model, 'new question')
    else:
        model._state = dict(generation='g', session_id='sess_test', turn_id='new', outcome='incomplete')
        model._needs_sync = True
        model._session_settings = model._current_settings()
        list(model.reconnect() if path == 'reconnect' else model.observe_history())
    assert not [r for r in caplog.records if r.msg == '回答为：%s']
