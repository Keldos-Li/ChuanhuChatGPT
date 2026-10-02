"""Session-bound configuration, using synthetic workers and local storage only."""
from copy import deepcopy

import gradio as gr
import pytest

from modules.agent_settings import load_settings, save_settings
from modules.agent_store import owner_identity
from modules.model_capabilities import reserve_submission
from test_agent_model import complete, env, request, select, send
from test_agent_input_model import staged, submit


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    import socket

    def fail(*args, **kwargs):
        raise AssertionError('Session configuration tests must remain offline')

    monkeypatch.setattr(socket, 'create_connection', fail)
    monkeypatch.setattr(socket.socket, 'connect', fail)
    monkeypatch.setattr(socket.socket, 'connect_ex', fail)


def test_new_chat_settings_apply_when_session_is_created(env, monkeypatch):
    calls, _ = complete(env, monkeypatch)
    model = select(env)
    settings = dict(model._tool_settings, network=False, web_search=False)
    model.save_agent_tools(settings)
    env.wrappers['set_system_prompt'](model, 'Instructions for this session', request=request())
    settings['network'] = True
    assert model._tool_settings['network'] is False
    assert load_settings(env.agents.shared.chuanhu_path, model._owner) == model._tool_settings
    assert load_settings(env.agents.shared.chuanhu_path, owner_identity('other'))['network'] is True

    send(env, model)
    command = next(call for call in calls if call['action'] == 'run')
    assert command['tool_settings']['network'] is False
    assert command['tool_settings']['web_search'] is False
    assert command['instructions'] == 'Instructions for this session'
    assert model._session_settings == model._current_settings()


@pytest.mark.parametrize('change', [
    {'network': False}, {'web_search': False}, {'search_mode': 'cached'},
    {'search_domains': ['example.com']}, {'computer_use': False},
    {'include_screenshots': True}, {'tool_search': False},
    {'programmatic_tool_calling': False}, {'functions': ['text_statistics']},
    {'mcp_servers': [{'server_label': 'reader', 'server_url': 'https://mcp.example.com',
                      'allowed_tools': ['read_issue']}]},
    {'code_execution': False, 'computer_use': False, 'programmatic_tool_calling': False},
])
def test_existing_session_rejects_every_tool_configuration_change(env, monkeypatch, change):
    complete(env, monkeypatch)
    model = select(env)
    send(env, model)
    snapshot = deepcopy(model._session_settings)
    defaults = load_settings(env.agents.shared.chuanhu_path, model._owner)

    with pytest.raises(gr.Error, match='会话创建后'):
        model.save_agent_tools(dict(model._tool_settings, **change))

    assert model._session_settings == snapshot
    assert model._current_settings() == snapshot
    assert model._tool_settings == snapshot['tools']
    assert load_settings(env.agents.shared.chuanhu_path, model._owner) == defaults


def test_existing_instructions_reject_direct_and_wrapped_setters(env, monkeypatch):
    complete(env, monkeypatch)
    model = select(env)
    send(env, model)
    snapshot = deepcopy(model._session_settings)
    for setter in (
        lambda: model.set_system_prompt('Changed instructions'),
        lambda: env.wrappers['set_system_prompt'](model, 'Changed instructions', request=request()),
    ):
        with pytest.raises(gr.Error, match='会话创建后'):
            setter()
    assert model.system_prompt == snapshot['instructions']
    model.auto_save(model.chatbot)
    assert model._store().get(model._owner, model.history_file_path)['settings'] == snapshot


def test_precreated_file_session_locks_configuration_before_first_turn(env, tmp_path, monkeypatch):
    model, paths, service, calls = staged(env, tmp_path, monkeypatch, ('upload-fail.txt',))
    model.set_system_prompt('Prepared session instructions')
    model.save_agent_tools(dict(model._tool_settings, network=False))
    submit(env, model, 'Not submitted', paths)
    session = model._state['session_id']
    snapshot = deepcopy(model._session_settings)
    assert model._state['outcome'] == 'not_started'
    assert not model.history and not service.sessions[session]['items']
    assert [call['action'] for call in calls] == ['prepare_inputs']

    with pytest.raises(gr.Error, match='会话创建后'):
        model.save_agent_tools(dict(model._tool_settings, network=True))
    with pytest.raises(gr.Error, match='会话创建后'):
        model.set_system_prompt('A different instruction')
    model._network_request('开启联网')
    assert model._pending_network is None
    assert model._session_settings == snapshot
    assert model._store().get(model._owner, model.history_file_path)['settings'] == snapshot
    assert service.sessions[session]['instructions'] == snapshot['instructions']
    assert service.sessions[session]['tools'] == snapshot['tools']


@pytest.mark.parametrize('command', ['开网', '请开启联网！', '关闭联网', '关闭云端联网'])
def test_network_commands_only_explain_locked_session_without_fork_state(env, monkeypatch, command):
    calls, _ = complete(env, monkeypatch)
    model = select(env)
    send(env, model)
    previous = (deepcopy(model.history), deepcopy(model._state), deepcopy(model._session_settings))
    count = len(calls)
    model._pending_network = False  # Clear obsolete pending state from the earlier workflow.
    output = send(env, model, command)
    assert len(calls) == count
    assert (model.history, model._state, model._session_settings) == previous
    assert model._pending_network is None and model._fork_previous is None
    assert env.agents.SESSION_CONFIG_LOCKED in output[-1][1]
    assert '按新配置新建并继续' not in output[-1][1]


def test_network_commands_configure_new_chat_without_creating_session(env):
    model = select(env)
    send(env, model, '请关闭联网。')
    assert model._tool_settings['network'] is False
    assert model._tool_settings['web_search'] is True
    assert not model._state.get('session_id') and not model.history
    assert load_settings(env.agents.shared.chuanhu_path, model._owner)['network'] is False
    send(env, model, '开启联网')
    assert model._tool_settings['network'] is True and model._pending_network is None


def test_idempotent_existing_setters_do_not_overwrite_new_chat_defaults(env, monkeypatch):
    complete(env, monkeypatch)
    model = select(env)
    send(env, model)
    snapshot = deepcopy(model._session_settings)
    new_defaults = dict(snapshot['tools'], network=False, web_search=False)
    save_settings(env.agents.shared.chuanhu_path, model._owner, new_defaults)

    def fail_save(*args, **kwargs):
        raise AssertionError('An unchanged existing session must not rewrite saved settings')

    monkeypatch.setattr(env.agents, 'save_settings', fail_save)
    model.save_agent_tools(deepcopy(snapshot['tools']))
    model.set_system_prompt(snapshot['instructions'])
    assert load_settings(env.agents.shared.chuanhu_path, model._owner) == new_defaults
    assert model._session_settings == snapshot


def test_restore_displays_effective_snapshot_even_when_account_defaults_changed(env, monkeypatch):
    complete(env, monkeypatch)
    model = select(env)
    model.set_system_prompt('Session-owned instruction')
    send(env, model)
    snapshot = deepcopy(model._session_settings)
    new_defaults = dict(snapshot['tools'], network=False, web_search=False)
    save_settings(env.agents.shared.chuanhu_path, model._owner, new_defaults)
    restored = select(env, browser='another-browser')
    assert restored._tool_settings == new_defaults
    restored.load_chat_history(model.history_file_path)
    assert restored._current_settings() == snapshot
    assert restored._tool_settings == snapshot['tools']
    assert restored.system_prompt == snapshot['instructions']
    # Recovery status does not make immutable fields editable; same-value
    # callbacks remain harmless even before cloud history is synchronized.
    restored.save_agent_tools(deepcopy(snapshot['tools']))
    restored.set_system_prompt(snapshot['instructions'])
    with pytest.raises(gr.Error, match='会话创建后'):
        restored.save_agent_tools(new_defaults)
    assert load_settings(env.agents.shared.chuanhu_path, model._owner) == new_defaults
    restored.auto_save(restored.chatbot)
    assert restored._store().get(restored._owner, restored.history_file_path)['settings'] == snapshot


@pytest.mark.parametrize('pending', ['running', 'queued'])
def test_first_submission_freezes_configuration_before_session_id_is_known(env, pending):
    model = select(env)
    snapshot = model._current_settings()
    generator = None
    if pending == 'running':
        generator = model.predict('Pending first message', [])
        next(generator)
    else:
        reserve_submission(model, 'Pending first message')
    assert not model._state.get('session_id')
    try:
        with pytest.raises(gr.Error):
            model.save_agent_tools(dict(model._tool_settings, network=False))
        with pytest.raises(gr.Error):
            model.set_system_prompt('Late instructions')
        with pytest.raises(gr.Error):
            model._network_request('关闭联网')
        assert model._current_settings() == snapshot
    finally:
        if generator is not None:
            generator.close()


@pytest.mark.parametrize('unavailable', ['unbound', 'retired'])
def test_configuration_setters_require_current_owned_model(env, unavailable):
    model = select(env)
    snapshot = model._current_settings()
    if unavailable == 'unbound':
        model._owner = None
    else:
        model.retire()
    for action in (
        lambda: model.save_agent_tools(dict(model._tool_settings, network=False)),
        lambda: model.set_system_prompt('Not authorized'),
        lambda: model._network_request('关闭联网'),
    ):
        with pytest.raises(gr.Error):
            action()
    assert model._current_settings() == snapshot


def test_wrong_owner_cannot_change_new_chat_instructions(env):
    model = select(env)
    snapshot = model._current_settings()
    with pytest.raises(gr.Error, match='不属于'):
        env.wrappers['set_system_prompt'](model, 'Wrong owner', request=request(username='other'))
    assert model._current_settings() == snapshot


def test_model_effort_still_updates_on_next_turn_in_same_locked_session(env, monkeypatch):
    calls, _ = complete(env, monkeypatch)
    model = select(env)
    send(env, model)
    session = model._state['session_id']
    snapshot = deepcopy(model._session_settings)
    start = len(calls)
    model.set_agent_model('gpt-6-sol', 'high')
    assert len(calls) == start
    send(env, model, 'Continue with the next model')
    assert [call['action'] for call in calls[start:]] == ['update', 'run', 'download']
    assert model._state['session_id'] == session
    assert model._session_settings['tools'] == snapshot['tools']
    assert model._session_settings['instructions'] == snapshot['instructions']
    assert model.agent_model_choice == ('gpt-6-sol', 'high')


def test_reset_enables_new_chat_configuration_and_preserves_old_binding(env, monkeypatch):
    complete(env, monkeypatch)
    model = select(env)
    send(env, model)
    old_path = model.history_file_path
    snapshot = deepcopy(model._session_settings)
    model.reset()
    model.save_agent_tools(dict(model._tool_settings, network=False))
    model.set_system_prompt('Instructions for the next chat')
    assert not model._state.get('session_id')
    assert model._tool_settings['network'] is False
    assert model.system_prompt == 'Instructions for the next chat'
    assert model._store().get(model._owner, old_path)['settings'] == snapshot


def test_legacy_fork_does_not_apply_obsolete_pending_configuration(env, monkeypatch):
    complete(env, monkeypatch)
    model = select(env)
    send(env, model)
    snapshot = deepcopy(model._session_settings)
    model._pending_network = False
    model.new_session_from_history()
    assert not model._state.get('session_id') and model._pending_network is None
    assert model._tool_settings == snapshot['tools']
    assert load_settings(env.agents.shared.chuanhu_path, model._owner) == snapshot['tools']
    # Internal compatibility callers can still create a fresh session first,
    # then choose its configuration. A failed fork restores the old snapshot.
    model.save_agent_tools(dict(model._tool_settings, network=False))
    model.set_system_prompt('New fork instructions')
    monkeypatch.setattr(env.agents, 'worker_messages', lambda command: iter([
        {'type': 'error', 'outcome': 'not_started', 'message': 'Synthetic creation failure'}]))
    send(env, model, 'New attempt')
    assert model._state.get('session_id') and model._current_settings() == snapshot
    assert model._tool_settings == snapshot['tools']
    assert model.system_prompt == snapshot['instructions']
