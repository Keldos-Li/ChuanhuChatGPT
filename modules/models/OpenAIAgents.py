"""A durable Agent session in the existing ChuanhuChat conversation surface."""
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
import tempfile
from threading import RLock
from uuid import uuid4

import gradio as gr
from modules import shared
from modules.agent_transport import worker_messages, connection_for_model
from modules.agent_store import BindingStore, owner_identity
from modules.model_capabilities import AGENT_CAPABILITIES, require_capability
from modules.agent_settings import load_settings, save_settings
from optional.agents.tools import validate_settings, tool_availability
from modules.presets import i18n
from .base_model import BaseLLMModel, HISTORY_DIR

TERMINAL = {'completed', 'failed', 'cancelled', 'not_started'}
STATUS = {'starting': '正在连接', 'in_progress': '正在运行', 'requires_action': '等待授权或登录',
          'restoring': '正在恢复历史', 'cancel_requested': '正在停止', 'cancelled': '已停止',
          'completed': '已完成', 'failed': '执行失败', 'not_started': '准备就绪',
          'incomplete': '暂时无法确认状态', 'uncertain': '暂时无法确认状态'}
_bindings = {}  # Backward-compatible test hook; authority lives in BindingStore.
_bindings_lock = RLock()
_session_locks = {}
_cancel_sessions = {}


def browser_owner(request):
    if request is None or not request.session_hash:
        raise gr.Error('需要在当前登录会话中操作')
    return owner_identity(request.username or '')


def fingerprint(history):
    return hashlib.sha256(json.dumps(history, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def display_signature(chatbot):
    return [(row[0], row[1]) for row in chatbot or [] if isinstance(row, (tuple, list)) and len(row) == 2 and isinstance(row[0], (str, type(None))) and isinstance(row[1], (str, type(None)))]


def _text_history(history):
    return [{'role': item['role'], 'content': item['content']} for item in history
            if isinstance(item, dict) and item.get('role') in ('user', 'assistant') and isinstance(item.get('content'), str)]


def _rows(history):
    rows = []
    for message in history:
        if message['role'] == 'user': rows.append([message['content'], None])
        elif rows and rows[-1][1] is None: rows[-1][1] = message['content']
        else: rows.append([None, message['content']])
    return rows


class OpenAIAgentsClient(BaseLLMModel):
    is_hosted_agent = True
    ui_capabilities = AGENT_CAPABILITIES

    def __init__(self, model_name, user_name='', owner=None, api_key=None):
        super().__init__(model_name=model_name, user=user_name, config={'stream': True})
        self._selection_name = model_name
        self.need_api_key = False
        self._connection_key = api_key or self.api_key
        self.api_key = None  # Credentials never enter exported chat metadata.
        self._default_instructions = self.system_prompt
        self._owner = owner
        self._lock = RLock()
        self._running = self._retired = False
        self._cancel_requested = self._cancel_sent = False
        self._conversation_id = uuid4().hex
        self._state = {'outcome': 'not_started'}
        self._display, self._artifacts, self._cloud_items = [], [], []
        self._answer_index = self._answer_row = None
        self._session_settings = None
        self._importing = self._needs_sync = False
        self._allow_text_tool = False
        self._reasoning = None
        self._pending_actions = []
        self._notice = ''
        self._unavailable = False
        self._connection_mismatch = False
        self._fork_previous = None
        self._auto_named = False
        self._first_prompt = None
        self._pending_network = None
        self.metadata = {}
        self._tool_settings = load_settings(shared.chuanhu_path, owner) if owner else validate_settings({})

    def bind_owner(self, request):
        owner = browser_owner(request)
        with self._lock:
            if self._owner is not None and self._owner != owner: raise gr.Error('此会话不属于当前登录用户')
            if self.user_name != (request.username or ''): raise gr.Error('聊天存储身份与当前登录用户不一致')
            if self._owner is None: self._tool_settings = load_settings(shared.chuanhu_path, owner)
            self._owner = owner

    def _connection(self):
        return connection_for_model(api_key=self._connection_key, api_host=self.api_host)

    def _connection_reference(self):
        from optional.agents.connection import AgentConnectionError
        try:
            connection = self._connection()
            return {key: connection.get(key) for key in ('base_url', 'organization', 'project')}
        except AgentConnectionError:
            return {'base_url': getattr(shared.state, 'openai_api_base', ''), 'organization': '', 'project': ''}

    def _worker(self, command):
        from optional.agents.connection import AgentConnectionError
        try:
            connection = self._connection()
        except AgentConnectionError as error:
            yield {'type': 'error', 'outcome': 'not_started', 'message': str(error)}
            return
        wire = dict(command, connection=connection)
        try:
            yield from worker_messages(wire)
        finally:
            wire.pop('response', None)
            wire.pop('connection', None)

    def _key(self): return self._owner, str(self.history_file_path).removesuffix('.json')
    def _store(self): return BindingStore(shared.chuanhu_path)

    def _remember(self):
        if not self._owner or self._importing: return
        if not self._state.get('session_id') and self._state.get('outcome') != 'uncertain': return
        state = {key: deepcopy(value) for key, value in self._state.items() if key not in ('required_actions', 'settings', 'items')}
        record = {'state': state, 'conversation_id': self._conversation_id, 'artifacts': deepcopy(self._artifacts),
                  'settings': deepcopy(self._session_settings), 'connection_ref': self._connection_reference(),
                  'items': deepcopy(self._cloud_items), 'auto_named': self._auto_named, 'first_prompt': self._first_prompt}
        self._store().put(self._owner, self.history_file_path, record)

    def adopt_local_history(self, original):
        self.history = deepcopy(original.history)
        self.history_file_path = original.history_file_path
        self.chatbot = deepcopy(getattr(original, 'chatbot', []))
        self._display = deepcopy(self.chatbot)
        self._restore_binding()

    def _restore_binding(self):
        self._fork_previous = None
        binding = None if self._importing or not self._owner else self._store().get(self._owner, self.history_file_path)
        self._pending_actions = []
        if binding:
            if binding.get('connection_ref') != self._connection_reference():
                self._notice = '当前连接配置与原会话不同，未连接云端；可恢复原配置，或明确按现配置新建并继续'
                self._state = dict(binding['state'], outcome='incomplete')
                self._needs_sync = True
                self._connection_mismatch = True
            else:
                self._state = binding['state']
                self._needs_sync = True
                self._connection_mismatch = False
            self._conversation_id = binding['conversation_id']
            self._auto_named = binding.get('auto_named', True)
            self._first_prompt = binding.get('first_prompt')
            self._artifacts = binding.get('artifacts', [])
            for artifact in self._artifacts:
                if artifact.get('status') == 'ready' and not Path(artifact.get('path', '')).is_file():
                    artifact.update(status='failed', error='本地下载缓存已失效，可重新获取')
                    artifact.pop('path', None)
            self._cloud_items = binding.get('items', [])
            self._session_settings = binding.get('settings')
            if self._session_settings:
                self.model_name = self._session_settings['model']
                self._reasoning = self._session_settings.get('reasoning')
                self.system_prompt = self._session_settings.get('instructions', self.system_prompt)
            self._notice = self._notice or '已找到原会话，发送前将按云端记录恢复历史'
        else:
            self._fresh()
            if self.history: self._notice = '将根据这份历史创建新的 Agent 会话：带入全部可用文字；不继承旧工具状态、沙盒文件和任务'

    def _fresh(self):
        self._fork_previous = None
        self._auto_named = False
        self._first_prompt = None
        self._pending_network = None
        self._state = {'outcome': 'not_started'}
        self._conversation_id = uuid4().hex
        self._artifacts, self._cloud_items, self._pending_actions = [], [], []
        self._answer_index = self._answer_row = self._session_settings = None
        self._needs_sync = self._connection_mismatch = self._unavailable = False

    def _assert_idle(self):
        if self._running or getattr(self, '_pending_send', None) or self._state.get('outcome') not in TERMINAL or self._needs_sync:
            raise gr.Error('当前任务仍在运行或状态尚待确认，请先停止或重新连接')

    def prepare_model_switch(self):
        with self._lock: self._assert_idle(); self._remember()
    def retire(self):
        with self._lock: self._retired = True
    def billing_info(self): return ''
    def set_key(self, new_key):
        with self._lock:
            self._assert_idle()
            self._connection_key = new_key
        return gr.update(value=new_key), '已更新连接密钥；下次连接时使用'
    def set_streaming(self, streaming):
        require_capability(self, 'output_mode')
    def _status(self, detail=''):
        return ' · '.join(part for part in (STATUS.get(self._state.get('outcome'), '暂时无法确认状态'), detail or self._notice) if part)

    def _current_settings(self):
        return {'model': self.model_name, 'reasoning': self._reasoning, 'instructions': self.system_prompt, 'tools': deepcopy(self._tool_settings)}

    def set_agent_model(self, model, reasoning):
        with self._lock:
            self._assert_idle()
            if reasoning == 'default': reasoning = None
            if not isinstance(model, str) or not model.strip(): raise gr.Error('请选择 Agent 子模型')
            old = self.model_name, self._reasoning
            if self._state.get('session_id'):
                confirmed = None
                for message in self._worker({'action': 'update', 'session_id': self._state['session_id'], 'model': model, 'reasoning': reasoning}):
                    if message.get('type') == 'error': raise gr.Error(message.get('message', '参数更新失败，保留原生效值'))
                    if message.get('type') == 'result': confirmed = message.get('settings')
                if not confirmed or confirmed.get('model') != model:
                    raise gr.Error('参数更新结果尚未确认，保留原生效值；请重新连接')
            self.model_name, self._reasoning = model, (confirmed.get('reasoning', reasoning) if self._state.get('session_id') else reasoning)
            if self._session_settings:
                self._session_settings.update(model=model, reasoning=self._reasoning)
            self._remember()
        return '参数已生效，将用于同一会话的下一轮'

    def save_agent_tools(self, settings):
        # Saving new defaults never changes the running session's tool set.
        with self._lock:
            self._tool_settings = save_settings(shared.chuanhu_path, self._owner, settings)
            return '新会话配置已保存。当前会话仍使用原设置；需按新配置新建并继续后生效' if self._state.get('session_id') else '新会话配置已保存'

    def new_session_from_history(self):
        with self._lock:
            if self._running or getattr(self, '_pending_send', None) or (not self._unavailable and (self._state.get('outcome') not in TERMINAL or self._needs_sync)):
                raise gr.Error('请先停止或确认当前任务状态，再创建独立会话')
            if self._pending_network is not None:
                self._tool_settings = save_settings(shared.chuanhu_path, self._owner, dict(self._tool_settings, network=self._pending_network))
            self.auto_save(self.chatbot)
            backup = {name: deepcopy(getattr(self, name)) for name in
                ('history_file_path', '_state', '_session_settings', '_artifacts', '_cloud_items', 'history', 'chatbot', '_display', '_conversation_id', '_auto_named', '_first_prompt', '_answer_index', '_answer_row', '_needs_sync', '_unavailable', '_connection_mismatch')}
            self.new_auto_history_filename()
            self._fresh()
            self._fork_previous = backup
            self._notice = '已保留原聊天。下一条消息将携带现有文字引用创建新会话；旧工具状态和文件不继承'
        return deepcopy(self.chatbot), self._status()

    def _cancel(self, generation):
        with self._lock:
            if self._state.get('generation') != generation or self._state.get('outcome') in TERMINAL: return
            if self._cancel_sent or not self._state.get('session_id') or not self._state.get('turn_id'): return
            self._cancel_sent = True
            command = {'action': 'cancel', 'session_id': self._state['session_id'], 'turn_id': self._state['turn_id']}
        cancel_key = (self._owner, command['session_id'])
        with _bindings_lock: _cancel_sessions[cancel_key] = _cancel_sessions.get(cancel_key, 0) + 1
        try:
            for message in self._worker(command):
                with self._lock:
                    if self._state.get('generation') != generation: return
                    if message.get('type') == 'error':
                        self._cancel_sent = False
                        self._notice = '尚未确认停止：' + message.get('message', '连接失败')
                    elif message.get('type') == 'result':
                        if self._state.get('outcome') not in TERMINAL:
                            self._state['outcome'] = message.get('outcome', 'cancel_requested')
                        self._notice = message.get('message', '已请求停止，等待云端确认')
                    self._remember()
        finally:
            with _bindings_lock:
                remaining = _cancel_sessions.get(cancel_key, 0) - 1
                if remaining > 0: _cancel_sessions[cancel_key] = remaining
                else: _cancel_sessions.pop(cancel_key, None)

    def interrupt(self):
        with self._lock:
            if self._state.get('outcome') in TERMINAL: return self._status('当前轮已结束')
            self._cancel_requested = True
            self._state['outcome'] = 'cancel_requested'
            generation = self._state.get('generation')
            self._pending_actions = []
            self._remember()
        self._cancel(generation)
        return self._status()

    def _sync_items(self, items):
        # Only server message items belong in conversational history. Tool output
        # and browser authentication values never get copied to text bubbles.
        unique = {}
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str): continue
            if item.get('type') != 'message' or item.get('role') not in ('user', 'assistant'): continue
            content = '\n'.join(part['text'] for part in item.get('content', []) if isinstance(part, dict) and part.get('type') in ('input_text', 'output_text', 'text') and isinstance(part.get('text'), str))
            unique[item['id']] = {'id': item['id'], 'role': item['role'], 'content': content, 'turn_id': item.get('turn_id')}
        self._cloud_items = list(unique.values())
        self.history = [{'role': item['role'], 'content': item['content']} for item in self._cloud_items]
        self._display = _rows(self.history)
        self._answer_index = len(self.history) - 1 if self.history and self.history[-1]['role'] == 'assistant' else None
        self._answer_row = len(self._display) - 1 if self._answer_index is not None else None

    def _accept(self, message, generation, *, restoring=False):
        if self._retired or self._state.get('generation') != generation: return False
        session = message.get('session_id')
        if session and self._state.get('session_id') and session != self._state['session_id']: return False
        turn = message.get('turn_id')
        if turn and self._state.get('turn_id') and turn != self._state['turn_id']:
            if not restoring: return False
            # An earlier stop belongs to the old exact turn, not a newer turn
            # discovered during cross-browser recovery.
            self._cancel_requested = self._cancel_sent = False
        for key in ('session_id', 'turn_id', 'baseline_turn_ids', 'submission_started'):
            if key in message and message[key] is not None: self._state[key] = message[key]
        outcome = message.get('outcome')
        if outcome and (not self._cancel_requested or outcome in TERMINAL): self._state['outcome'] = outcome
        if (message.get('sync_complete') or message.get('history_authoritative')) and isinstance(message.get('items'), list):
            self._sync_items(message['items'])
            if message.get('sync_complete'): self._needs_sync = False
        elif self._answer_index is not None and 'text' in message:
            self.history[self._answer_index]['content'] = str(message['text'])
            self._display[self._answer_row][1] = self.history[self._answer_index]['content']
        if message.get('sync_complete') is False:
            self._needs_sync = True
            self._notice = '当前轮已结束，但云端历史尚未完整同步；回答已保留，请重新连接后继续'
        if 'required_actions' in message:
            self._pending_actions = [] if self._cancel_requested else deepcopy(message['required_actions'])
        if self._state.get('outcome') in TERMINAL: self._pending_actions = []
        settings = message.get('settings') or {}
        if restoring and settings.get('agent') and self._session_settings:
            agent = settings['agent']
            self.model_name = agent.get('model', self.model_name)
            self._reasoning = (agent.get('reasoning') or {}).get('effort')
            self._session_settings.update(model=self.model_name, reasoning=self._reasoning)
        self._remember()
        return True

    def _download(self, generation, artifact_ids=None):
        first = True
        for message in self._worker({'action': 'download', 'session_id': self._state['session_id'], 'artifact_ids': artifact_ids}):
            if message.get('type') == 'error':
                self._notice = '回答已保留，文件获取失败，可重试：' + message.get('message', '')
                yield deepcopy(self._display), self._status()
                return
            if 'artifacts' not in message: continue
            records = message['artifacts']
            root = Path(tempfile.gettempdir()).resolve()
            for record in records:
                if record.get('status') != 'ready': continue
                path = Path(record.get('path', ''))
                if path.is_symlink() or not path.is_file() or root not in path.resolve().parents or not any(parent.name.startswith('chuanhu-agent-artifacts-') for parent in path.parents):
                    record.update(status='failed', error='文件缓存校验失败，请重新获取')
                    record.pop('path', None)
            with self._lock:
                if self._state.get('generation') != generation or self._retired: return
                previous = {record['id']: record for record in self._artifacts}
                if first and artifact_ids is None: previous = {}
                previous.update((record['id'], record) for record in records)
                self._artifacts = list(previous.values())
                self._remember()
                first = False
            yield deepcopy(self._display), self._status()

    def retry_artifact(self, artifact_id):
        if artifact_id not in {record['id'] for record in self._artifacts}: raise gr.Error('文件不属于当前会话')
        yield from self._download(self._state.get('generation'), [artifact_id])

    def _network_request(self, inputs):
        commands = {'开网': True, '开启联网': True, '允许联网': True, '打开联网': True,
                    '关网': False, '关闭联网': False, '禁用联网': False, '关闭云端联网': False}
        normalized = inputs.strip().strip('。！!').removeprefix('请')
        if normalized not in commands: return None
        with self._lock:
            if self._retired: raise gr.Error('聊天已切换，未修改旧会话设置')
            self._assert_idle()
            desired = commands[normalized]
            effective = (self._session_settings or {}).get('tools', self._tool_settings)['network']
            word = '开启' if desired else '关闭'
            if self._state.get('session_id') and effective != desired:
                self._pending_network = desired
                self._notice = f'现有会话无法直接{word}云端执行环境联网。请选择“按新配置新建并继续”确认；只携带文字历史，原会话保持原设置。内置网页搜索单独配置'
            elif not self._state.get('session_id'):
                self._tool_settings = save_settings(shared.chuanhu_path, self._owner, dict(self._tool_settings, network=desired))
                self._notice = f'新会话的云端执行环境联网已设为{word}；请输入任务。内置网页搜索单独配置'
            else:
                self._notice = f'当前会话的云端执行环境联网已经{word}；内置网页搜索单独配置'
            return deepcopy(self._display), self._status()

    def predict(self, inputs, chatbot, use_websearch=False, files=None, reply_language=None, should_check_token_count=True):
        if not self._owner: raise gr.Error('需要在当前登录会话中发送')
        if not isinstance(inputs, str) or not inputs.strip(): raise gr.Error('请输入文字任务')
        if use_websearch or files: raise gr.Error('当前 Agent 不支持原附件或外部搜索入口，请使用 Agent 工具配置')
        if self._needs_sync:
            yield from self.reconnect()
            chatbot = self.chatbot
        network_result = self._network_request(inputs)
        if network_result is not None:
            yield network_result
            return
        with self._lock:
            if self._retired or (self._display and display_signature(chatbot) != display_signature(self._display)): raise gr.Error('聊天内容已变化，请在当前聊天中重新发送')
            self._assert_idle()
            reservation = (self._owner, self._state.get('session_id') or self._key()[1])
            with _bindings_lock:
                if reservation in _session_locks or reservation in _cancel_sessions:
                    raise gr.Error('另一窗口正在提交此会话，请等待该任务结束或重新连接')
                _session_locks[reservation] = self
            settings = self._current_settings()
            if self._state.get('session_id') and self._session_settings:
                # Saved defaults apply only after an explicit fork. Continuing
                # the original chat always sends its actual effective settings.
                settings = deepcopy(self._session_settings)
            previous = (deepcopy(self._state), deepcopy(self.history), deepcopy(self._display), self._answer_index, self._answer_row)
            reference = _text_history(self.history) if not self._state.get('session_id') else None
            if not self._state.get('session_id'): self._first_prompt = inputs
            generation = uuid4().hex
            self._state = dict(self._state, generation=generation, turn_id=None, outcome='starting', baseline_turn_ids=None, submission_started=False)
            self._display = [list(row) for row in chatbot or []] + [[inputs, '']]
            self.history.extend([{'role': 'user', 'content': inputs}, {'role': 'assistant', 'content': ''}])
            self._answer_index, self._answer_row = len(self.history) - 1, len(self._display) - 1
            self._running = True
            self._cancel_requested = self._cancel_sent = False
            self._notice = ''
            command = {'action': 'run', 'prompt': inputs, 'model': self.model_name, 'reasoning': self._reasoning,
                       'instructions': settings['instructions'], 'tool_settings': deepcopy(settings['tools']),
                       'session_id': self._state.get('session_id'), 'run_id': generation, 'history_reference': reference}
        started = terminal = False
        try:
            yield deepcopy(self._display), self._status()
            with self._lock:
                if self._cancel_requested: return
                started = True
                self._session_settings = settings
                self._remember()
            for message in self._worker(command):
                with self._lock:
                    if not self._accept(message, generation): continue
                    detail = str(message.get('message') or '')
                    if message.get('type') == 'error':
                        if self._state.get('outcome') not in TERMINAL: self._state['outcome'] = 'incomplete' if self._state.get('session_id') else 'uncertain'
                        terminal = self._state.get('outcome') in TERMINAL
                        self._notice = detail
                    elif message.get('type') == 'result': terminal = self._state.get('outcome') in TERMINAL
                if self._cancel_requested: self._cancel(generation)
                yield deepcopy(self._display), self._status(detail)
            if self._state.get('outcome') in ('completed', 'cancelled', 'failed') and self._state.get('session_id'):
                yield from self._download(generation)
                yield deepcopy(self._display), self._status()
        finally:
            with self._lock:
                if self._state.get('generation') == generation:
                    if not started:
                        self._state, self.history, self._display, self._answer_index, self._answer_row = previous
                    elif not terminal and self._state.get('outcome') not in TERMINAL:
                        self._state['outcome'] = 'incomplete' if self._state.get('session_id') else 'uncertain'
                    self._running = False
                    with _bindings_lock:
                        if _session_locks.get(reservation) is self: _session_locks.pop(reservation, None)
                    self.chatbot = deepcopy(self._display)
                    self._remember()
                    if started:
                        self.auto_save(self.chatbot)
                        if self._fork_previous and self._state.get('outcome') == 'not_started' and not self._state.get('session_id'):
                            # Keep the failed attempt as a separate local record,
                            # and return control to the original preserved session.
                            for name, value in self._fork_previous.items(): setattr(self, name, value)
                            self._fork_previous = None
                            self._notice = '新会话未创建，已回到原会话；本次未发送内容另存于本地历史，新配置仍已保存'
                        elif self._state.get('session_id'):
                            self._fork_previous = None
        if self._notice.startswith('新会话未创建'):
            yield deepcopy(self._display), self._status()

    def reconnect(self):
        empty_result = None
        with self._lock:
            if self._running or self._retired: raise gr.Error('当前连接仍在运行')
            if getattr(self, '_connection_mismatch', False): raise gr.Error(self._notice)
            session = self._state.get('session_id')
            if not session and self._state.get('outcome') != 'uncertain':
                empty_result = (deepcopy(self._display), self._status())
            else:
                generation = self._state.get('generation') or uuid4().hex
                self._state['generation'] = generation
                self._running = True
                self._notice = ''
                command = {'action': 'recover' if session else 'recover_unknown', 'session_id': session,
                           'turn_id': self._state.get('turn_id'), 'run_id': generation,
                           'baseline_turn_ids': self._state.get('baseline_turn_ids'), 'submission_started': self._state.get('submission_started') is True,
                           'tool_settings': (self._session_settings or {}).get('tools', self._tool_settings)}
        if empty_result is not None:
            yield empty_result
            return
        try:
            yield deepcopy(self._display), '正在恢复历史'
            for message in self._worker(command):
                with self._lock:
                    if message.get('type') == 'error':
                        if (message.get('diagnostics') or {}).get('status_code') in (403, 404, 410): self._unavailable = True
                        self._notice = '历史尚未同步完整，现有内容已保留：' + message.get('message', '')
                        self._needs_sync = True
                        message = {key: value for key, value in message.items() if key not in ('outcome', 'items', 'sync_complete')}
                    if not self._accept(message, generation, restoring=True): continue
                    self.chatbot = deepcopy(self._display)
                if self._cancel_requested: self._cancel(generation)
                yield deepcopy(self._display), self._status()
            if self._state.get('outcome') in TERMINAL and self._state.get('session_id'):
                yield from self._download(generation)
                yield deepcopy(self._display), self._status()
        finally:
            with self._lock:
                self._running = False
                self.chatbot = deepcopy(self._display)
                self._remember()
                self.auto_save(self.chatbot)

    def retry(self, chatbot, *args, **kwargs):
        raise gr.Error('Agent 不支持重新生成。需要恢复断线时请重新连接原会话')
        yield  # Keep the ordinary model protocol, without a regenerate action.

    def respond_browser(self, request_id, response):
        with self._lock:
            if self._cancel_requested or self._retired or self._state.get('outcome') in TERMINAL: raise gr.Error('当前任务已结束或正在停止')
            if request_id not in {card['request_id'] for card in self._pending_actions}: raise gr.Error('该网站请求已经失效，请重新连接')
            command = {'action': 'browser_response', 'session_id': self._state['session_id'], 'turn_id': self._state['turn_id'], 'request_id': request_id, 'response': response}
        accepted = False
        try:
            for message in self._worker(command):
                if message.get('type') == 'error': raise gr.Error(message.get('message', '提交结果尚待确认，请重新连接'))
                accepted = message.get('accepted') is True
        finally: command.pop('response', None)
        if not accepted: raise gr.Error('提交结果尚待确认，请重新连接，勿重复提交')
        with self._lock:
            self._pending_actions = [card for card in self._pending_actions if card['request_id'] != request_id]
            self._notice = '已提交网站请求，等待任务继续；这不表示已经登录成功'
        return self._status()

    def auto_save(self, chatbot=None):
        with self._lock:
            super().auto_save(chatbot)
            self._remember()

    def load_chat_history(self, new_history_file_path=None):
        with self._lock:
            self._assert_idle()
            self._remember()
            chosen = new_history_file_path or self.history_file_path
            if chosen:
                root = (Path(HISTORY_DIR) / self.user_name).resolve()
                candidate = Path(chosen) if Path(chosen).is_absolute() else root / chosen
                if candidate.resolve().parent != root:
                    raise gr.Error('只能读取当前登录用户的聊天历史')
            explicit_instructions = self.system_prompt
            result = list(super().load_chat_history(new_history_file_path))
            self.system_prompt = explicit_instructions  # Imported history is reference data, never authority.
            self.metadata = {}
            self.stream = True
            self.history = _text_history(self.history)
            self.chatbot = _rows(self.history)
            self._display = deepcopy(self.chatbot)
            self._notice = ''
            self._restore_binding()
            result[1], result[2], result[14] = self.system_prompt, gr.update(value=self.chatbot), True
            return tuple(result)

    def upload_chat_history(self, new_history_file_content=None):
        with self._lock:
            self._assert_idle()
            self._remember()
            self._importing = True
            try: return super().upload_chat_history(new_history_file_content)
            finally: self._importing = False

    def reset(self, remain_system_prompt=False):
        with self._lock:
            self._assert_idle()
            self._remember()
            result = list(super().reset(remain_system_prompt))
            if not remain_system_prompt: self.system_prompt = self._default_instructions
            self.stream = True
            result[3], result[15] = self.system_prompt, True
            self._fresh()
            self._display, self.chatbot = [], []
            self._notice = ''
            return tuple(result)

    def rename_chat_history(self, filename):
        with self._lock:
            self._assert_idle()
            old = self.history_file_path
            result = super().rename_chat_history(filename)
            self._auto_named = True
            self._store().rename(self._owner, old, self.history_file_path)
            self._remember()
            return result

    def delete_chat_history(self, filename):
        with self._lock:
            self._assert_idle()
            result = super().delete_chat_history(filename)
            if self._owner: self._store().forget(self._owner, filename)
            return result

    def delete_first_conversation(self): raise gr.Error('Agent 云端历史不支持本地回退')
    def delete_last_conversation(self, chatbot): raise gr.Error('Agent 云端历史不支持本地回退')
    def auto_name_chat_history(self, name_chat_method, user_question, single_turn_checkbox):
        first_turn = len([item for item in self.history if item.get('role') == 'user']) == 1
        if self._state.get('outcome') not in TERMINAL or self._needs_sync or not first_turn or self._auto_named or single_turn_checkbox:
            return gr.update()
        question = self._first_prompt or next(item['content'] for item in self.history if item['role'] == 'user')
        title = ''
        if name_chat_method == i18n('naming.by_model_summary'):
            for message in self._worker({'action': 'title', 'model': self.model_name, 'history': _text_history(self.history)}):
                if message.get('type') == 'result': title = message.get('title', '')
            if not title.strip(): self._notice = '模型标题未生成，已使用首问命名'
        elif name_chat_method != i18n('naming.by_first_question'):
            return gr.update()
        title = title.strip() or question[:16]
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip().strip('.')
        return self.rename_chat_history((title or 'Agent 聊天') + '.json')
    def handle_file_upload(self, *args): raise gr.Error('当前 Agent 不支持输入附件')
    def summarize_index(self, *args): raise gr.Error('当前 Agent 不支持本地知识库入口')
