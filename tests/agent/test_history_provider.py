"""验证 Agent 会话的模型锁定与历史切换边界。"""
import pytest
import json

from agent_fixtures import complete, env, request, select, send


def test_started_agent_rejects_manual_provider_change(env, monkeypatch):
    complete(env, monkeypatch)
    model = select(env)
    send(env, model)
    before = (model.history_file_path, list(model.history), model._state.copy())
    result = env.factory.change_model('GPT3.5 Turbo', None, None, None, None,
                                     None, '', model, request())
    assert result[0] is model
    assert result[8]['value'] == 'OpenAI Agent'
    assert not model._retired
    assert (model.history_file_path, model.history, model._state) == before


def test_new_chat_unlocks_manual_provider_change(env, monkeypatch):
    complete(env, monkeypatch)
    model = select(env)
    send(env, model)
    model.reset()
    result = env.factory.change_model('GPT3.5 Turbo', None, None, None, None,
                                     None, '', model, request())
    assert result[0] is not model
    assert not getattr(result[0], 'is_hosted_agent', False)


def test_agent_internal_model_settings_remain_changeable(env, monkeypatch):
    calls, _ = complete(env, monkeypatch)
    model = select(env)
    send(env, model)
    model.set_agent_model(model.model_name, 'low')
    send(env, model, '继续')
    assert model._reasoning == 'low'
    assert any(call['action'] == 'update' for call in calls)


def test_history_restores_provider_parameters_and_prompt_without_saved_key(env, monkeypatch):
    from modules.history_selection import load_history_model
    monkeypatch.setattr(env.presets, 'HISTORY_DIR', str(env.history_dir), raising=False)
    ordinary = select(env, name='GPT3.5 Turbo')
    ordinary.system_prompt = '恢复的系统提示'
    ordinary.temperature = 0.37
    ordinary.top_p = 0.81
    send(env, ordinary)
    filename = ordinary.history_file_path
    document = env.history_dir / (filename.removesuffix('.json') + '.json')
    saved = json.loads(document.read_text())
    assert saved['model_selection'] == 'GPT3.5 Turbo'
    assert 'api_key' not in saved
    saved['api_key'] = '不得加载的历史密钥'
    document.write_text(json.dumps(saved))
    complete(env, monkeypatch)
    agent = select(env)
    send(env, agent)
    result = load_history_model(agent, filename, request())
    restored = result[0]
    assert not getattr(restored, 'is_hosted_agent', False)
    assert result[1]['value'] == 'GPT3.5 Turbo'
    assert restored.system_prompt == '恢复的系统提示'
    assert restored.temperature == 0.37 and restored.top_p == 0.81
    assert restored.api_key != saved['api_key']
    assert agent._retired


def test_history_restores_agent_session_and_last_internal_model(env, monkeypatch):
    from modules.history_selection import load_history_model
    monkeypatch.setattr(env.presets, 'HISTORY_DIR', str(env.history_dir), raising=False)
    complete(env, monkeypatch)
    agent = select(env)
    send(env, agent)
    agent.set_agent_model('gpt-6-sol', 'low')
    send(env, agent, '继续')
    filename = agent.history_file_path
    ordinary = select(env, name='GPT3.5 Turbo')
    result = load_history_model(ordinary, filename, request())
    restored = result[0]
    assert restored.is_hosted_agent
    assert restored.model_name == 'gpt-6-sol' and restored._reasoning == 'low'
    assert restored._state['session_id'] == agent._state['session_id']
    assert result[1]['value'] == 'OpenAI Agent'


def test_history_selection_rejects_foreign_paths_and_owner(env, tmp_path, monkeypatch):
    from modules.history_selection import load_history_model
    monkeypatch.setattr(env.presets, 'HISTORY_DIR', str(env.history_dir), raising=False)
    model = select(env)
    with pytest.raises(Exception, match='当前登录用户'):
        load_history_model(model, str(tmp_path / 'outside.json'), request())
    with pytest.raises(Exception, match='当前登录用户'):
        load_history_model(model, 'anything', request(username='someone-else'))


def test_worker_identity_cannot_be_overridden_by_tool_configuration(env, monkeypatch):
    model = select(env, username='alice')
    frames = []
    monkeypatch.setattr(env.agents, 'worker_messages', lambda command: frames.append(command.copy()) or iter([]))
    list(model._worker({'action': 'capabilities', 'owner': 'bob'}))
    assert frames[0]['owner'] == 'alice'


def test_terminal_navigation_preserves_completed_reply_before_release(env, monkeypatch):
    model=select(env); saved=[]
    monkeypatch.setattr(model,'auto_save',lambda chat=None:saved.append((model.history_file_path,list(model.history))))
    def worker(command):
        if command['action']=='run':
            yield dict(type='progress',session_id='sess_test',turn_id='turn',outcome='completed',text='Completed answer')
            yield dict(type='result',session_id='sess_test',turn_id='turn',outcome='completed',text='Completed answer')
        elif command['action']=='download':yield dict(type='result',artifacts=[])
        else:raise AssertionError(command['action'])
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    iterator=model.predict('Task',[])
    next(iterator); next(iterator)
    assert model._running is False and model._display==[['Task','Completed answer']]
    model.reset()
    list(iterator)
    assert any(any(item.get('content')=='Completed answer' for item in history) for _,history in saved), saved


def test_terminal_history_navigation_saves_and_preserves_old_reply(env, monkeypatch):
    from modules.history_selection import load_history_model
    monkeypatch.setattr(env.presets,'HISTORY_DIR',str(env.history_dir),raising=False)
    ordinary=select(env,name='GPT3.5 Turbo'); send(env,ordinary,'Other task')
    other=ordinary.history_file_path
    model=select(env)
    def worker(command):
        if command['action']=='run':
            yield dict(type='progress',session_id='sess_test',turn_id='turn',outcome='completed',text='Completed answer')
            yield dict(type='result',session_id='sess_test',turn_id='turn',outcome='completed',text='Completed answer')
        elif command['action']=='download':yield dict(type='result',artifacts=[])
        else:raise AssertionError(command['action'])
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    iterator=model.predict('Task',[]);next(iterator);next(iterator)
    name=model.history_file_path
    path=env.history_dir/(name.removesuffix('.json')+'.json')
    assert any(item.get('content')=='Completed answer' for item in json.loads(path.read_text())['history'])
    restored=load_history_model(model,other,request())[0]
    assert restored is not model and model._retired
    before=list(restored.history)
    list(iterator)
    assert restored.history==before
    assert any(item.get('content')=='Completed answer' for item in json.loads(path.read_text())['history'])


@pytest.mark.parametrize('name', ['GPT3.5 Turbo', 'OpenAI Agent'])
def test_new_chat_lists_saved_history_only_and_first_send_saves_reserved_path(env, monkeypatch, name):
    from modules.history_selection import load_history_model
    monkeypatch.setattr(env.presets, 'HISTORY_DIR', str(env.history_dir), raising=False)
    complete(env, monkeypatch)
    model = select(env, name=name)
    send(env, model, 'Saved conversation')
    saved = model.history_file_path
    old_chat = [list(row) for row in model.chatbot]
    reset = model.reset()
    reserved = model.history_file_path
    selector = reset[2]
    assert selector.value is None
    choices = [choice[0] for choice in selector.choices]
    assert saved.removesuffix('.json') in choices
    assert reserved.removesuffix('.json') not in choices
    assert not (env.history_dir / reserved).exists()
    send(env, model, 'First new message')
    assert model.history_file_path == reserved
    assert (env.history_dir / reserved).is_file()
    from modules.models import base_model
    assert reserved.removesuffix('.json') in base_model.get_history_names(model.user_name)
    restored = load_history_model(model, saved, request())[0]
    assert restored.chatbot == old_chat


@pytest.mark.parametrize('contents', ['{broken json', '{}', '{"history": [], "chatbot": "invalid"}'])
def test_invalid_saved_history_remains_an_error_after_reset(env, monkeypatch, contents):
    from modules.history_selection import load_history_model
    monkeypatch.setattr(env.presets, 'HISTORY_DIR', str(env.history_dir), raising=False)
    model = select(env, name='GPT3.5 Turbo')
    model.reset()
    (env.history_dir / 'broken.json').write_text(contents)
    with pytest.raises(Exception, match='历史记录无法读取或格式无效'):
        load_history_model(model, 'broken', request())
    with pytest.raises(Exception, match='历史记录无法读取或格式无效'):
        load_history_model(model, 'missing', request())
