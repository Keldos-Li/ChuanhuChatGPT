"""Official hosted Agent as a main-chat model, with server-private capabilities."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from uuid import uuid4

import gradio as gr
from modules import shared
from modules.agent_transport import worker_messages
from modules.presets import i18n
from .base_model import BaseLLMModel

TERMINAL = {'completed', 'failed', 'cancelled', 'not_started'}
MAX_OUTPUT = 100000
# Values are created by this process, never deserialized from a history upload.
_bindings = {}
_bindings_lock = RLock()


def browser_owner(request):
    if request is None or not request.session_hash:
        raise gr.Error(i18n('model.openai_agent.browser_required'))
    return hashlib.sha256(((request.username or '') + ':' + request.session_hash).encode()).hexdigest()


def fingerprint(history):
    return hashlib.sha256(json.dumps(history, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def display_signature(chatbot):
    # File bubbles are output-only; they never become an input to the Agent.
    return [(row[0], row[1]) for row in chatbot or [] if isinstance(row, (tuple, list))
            and len(row) == 2 and isinstance(row[0], str) and isinstance(row[1], (str, type(None)))]


class OpenAIAgentsClient(BaseLLMModel):
    is_hosted_agent = True

    def __init__(self, model_name, user_name='', owner=None):
        super().__init__(model_name=model_name, user=user_name,
                         config={'api_key': None, 'api_host': None, 'stream': True})
        self._selection_name = model_name
        self.need_api_key = False
        self.api_key = self.api_host = None
        self._default_instructions = self.system_prompt
        self._owner = owner
        self._lock = RLock()
        self._running = False
        self._retired = False
        self._cancel_requested = False
        self._cancel_sent = False
        self._conversation_id = uuid4().hex
        self._state = {'outcome': 'not_started'}
        self._display = []
        self._artifacts = []
        self._answer_index = None
        self._answer_row = None
        self._session_settings = None
        self._importing = False
        self._allow_text_tool = self.metadata.get('allow_text_tool') is True
        self.metadata = {}  # Ordinary JSON metadata cannot grant tool/session rights.

    def bind_owner(self, request):
        owner = browser_owner(request)
        with self._lock:
            if self._owner is not None and self._owner != owner:
                raise gr.Error(i18n('model.openai_agent.wrong_owner'))
            self._owner = owner

    def _key(self):
        return self._owner, str(self.history_file_path).removesuffix('.json')

    def _remember(self):
        if not self._owner or self._importing:
            return
        binding = {'fingerprint': fingerprint(self.history), 'state': deepcopy(self._state),
                   'conversation_id': self._conversation_id, 'artifacts': list(self._artifacts),
                   'answer_index': self._answer_index, 'answer_row': self._answer_row, 'display': deepcopy(self._display), 'settings': self._session_settings}
        with _bindings_lock:
            _bindings[self._key()] = binding
        folder = Path(shared.chuanhu_path) / 'agent_data' / self._owner
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (self._conversation_id + '.json')
        record = {key: self._state.get(key) for key in ('session_id', 'turn_id', 'outcome', 'generation', 'baseline_turn_ids', 'submission_started')}
        record.update(owner=self._owner, conversation_id=self._conversation_id,
                      history_fingerprint=binding['fingerprint'])
        temporary = path.with_suffix('.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(record, output)
        temporary.replace(path)

    def adopt_local_history(self, original):
        # Visible transcript survives model switches; it is not automatically sent.
        self.history = deepcopy(original.history)
        self.history_file_path = original.history_file_path
        self.chatbot = deepcopy(getattr(original, 'chatbot', []))
        self._display = deepcopy(self.chatbot)
        self._restore_binding()

    def _restore_binding(self):
        with _bindings_lock:
            binding = None if self._importing else deepcopy(_bindings.get(self._key()))
        if binding and binding['fingerprint'] == fingerprint(self.history):
            self._state = binding['state']
            self._conversation_id = binding['conversation_id']
            self._artifacts = binding['artifacts']
            self._answer_index = binding['answer_index']
            self._answer_row = binding['answer_row']
            self._display = binding['display']
            self.chatbot = deepcopy(self._display)
            self._session_settings = binding['settings']
            if self._session_settings:
                self.system_prompt = self._session_settings[1]
        else:
            self._state = {'outcome': 'not_started'}
            self._conversation_id = uuid4().hex
            self._artifacts = []
            self._answer_index = None
            self._answer_row = None
            self._session_settings = None

    def _assert_idle(self):
        if self._running or self._state.get('outcome') not in TERMINAL:
            raise gr.Error(i18n('model.openai_agent.reconcile_first'))

    def prepare_model_switch(self):
        with self._lock:
            self._assert_idle()
            self._remember()

    def retire(self):
        with self._lock:
            self._retired = True

    def billing_info(self):
        return i18n('model.openai_agent.billing')  # No parallel provider request.

    def set_key(self, new_key):
        return gr.update(), i18n('model.openai_agent.dedicated_key')

    def set_streaming(self, streaming):
        self.stream = True

    def _status(self, detail=''):
        return i18n('model.openai_agent.status').format(outcome=self._state.get('outcome', 'not_started'), detail=detail)

    def _cancel(self, generation):
        with self._lock:
            if self._state.get('generation') != generation or self._state.get('outcome') in TERMINAL:
                return
            if self._cancel_sent or not self._state.get('session_id') or not self._state.get('turn_id'):
                return
            self._cancel_sent = True
            command = {'action': 'cancel', 'session_id': self._state['session_id']}
        for message in worker_messages(command):
            if message.get('type') == 'error':
                with self._lock:
                    if self._state.get('generation') == generation:
                        self._cancel_sent = False
                return

    def interrupt(self):
        with self._lock:
            if self._state.get('outcome') in TERMINAL:
                return self._status(i18n('model.openai_agent.already_finished'))
            self._cancel_requested = True
            self._state['outcome'] = 'cancel_requested'
            generation = self._state.get('generation')
            self._remember()
        self._cancel(generation)
        return self._status(i18n('model.openai_agent.cancel_pending'))

    def _accept(self, message, generation):
        if self._retired or self._state.get('generation') != generation:
            return False
        for key in ('session_id', 'turn_id'):
            if message.get(key):
                self._state[key] = message[key]
        for key in ('baseline_turn_ids', 'submission_started'):
            if key in message:
                self._state[key] = message[key]
        outcome = message.get('outcome')
        if outcome and (not self._cancel_requested or outcome in TERMINAL):
            self._state['outcome'] = outcome
        if self._answer_index is not None and message.get('text'):
            self.history[self._answer_index]['content'] = str(message['text'])[:MAX_OUTPUT]
            self._display[self._answer_row][1] = self.history[self._answer_index]['content']
        self._remember()
        return True

    def _download(self, generation):
        files = []
        for message in worker_messages({'action': 'download', 'session_id': self._state['session_id']}):
            if message.get('type') == 'error':
                raise gr.Error(str(message.get('message') or i18n('model.openai_agent.invalid_artifacts'))[:1000])
            if message.get('type') == 'result':
                files = message.get('files', [])
        if not isinstance(files, list) or len(files) > 20:
            raise gr.Error(i18n('model.openai_agent.invalid_artifacts'))
        total = 0
        for filename in files:
            if not isinstance(filename, str):
                raise gr.Error(i18n('model.openai_agent.invalid_artifacts'))
            path = Path(filename)
            root = Path(tempfile.gettempdir()).resolve()
            if (path.is_symlink() or root not in path.resolve().parents
                    or not path.parent.name.startswith('chuanhu-agent-artifacts-') or not path.is_file()):
                raise gr.Error(i18n('model.openai_agent.invalid_artifacts'))
            size = path.stat().st_size
            total += size
            if size > 10 * 1024 * 1024 or total > 50 * 1024 * 1024:
                raise gr.Error(i18n('model.openai_agent.invalid_artifacts'))
        with self._lock:
            if self._state.get('generation') != generation or self._retired:
                return
            self._artifacts = files
            # Native Chatbot file bubbles register bounded files in Gradio cache.
            # Progress and file metadata stay out of ordinary assistant history.
            self._display.extend([[None, (filename, Path(filename).name)] for filename in files])
            self._remember()

    def predict(self, inputs, chatbot, use_websearch=False, files=None, reply_language=None, should_check_token_count=True):
        if not self._owner:
            raise gr.Error(i18n('model.openai_agent.browser_required'))
        if not isinstance(inputs, str) or not inputs.strip() or len(inputs) > 20000:
            raise gr.Error(i18n('model.openai_agent.text_only'))
        if use_websearch or files:
            raise gr.Error(i18n('model.openai_agent.no_local_tools'))
        with self._lock:
            if self._retired or (self._display and display_signature(chatbot) != display_signature(self._display)):
                raise gr.Error(i18n('model.openai_agent.stale_input'))
            if self._running or self._state.get('outcome') not in TERMINAL:
                raise gr.Error(i18n('model.openai_agent.reconcile_first'))
            settings = (self.model_name, self.system_prompt, self._allow_text_tool)
            if self._state.get('session_id') and self._session_settings != settings:
                raise gr.Error(i18n('model.openai_agent.settings_locked'))
            previous = (deepcopy(self._state), deepcopy(self.history), deepcopy(self._display), self._answer_index, self._answer_row)
            generation = uuid4().hex
            self._state = dict(self._state, generation=generation, turn_id=None, outcome='starting',
                               baseline_turn_ids=None, submission_started=False)
            self._display = [list(row) for row in chatbot or []] + [[inputs, '']]
            self.history.extend([{'role': 'user', 'content': inputs}, {'role': 'assistant', 'content': ''}])
            self._answer_index = len(self.history) - 1
            self._answer_row = len(self._display) - 1
            self._running = True
            self._cancel_requested = self._cancel_sent = False
            command = {'action': 'run', 'prompt': inputs, 'model': self.model_name,
                       'instructions': self.system_prompt, 'allow_text_tool': self._allow_text_tool,
                       'session_id': self._state.get('session_id'), 'run_id': generation}
        started = False
        terminal = False
        detail = ''
        try:
            yield deepcopy(self._display), self._status()
            with self._lock:
                unsent = self._cancel_requested
                if unsent:
                    self._state, self.history, self._display, self._answer_index, self._answer_row = previous
                    self._running = False
                else:
                    started = True
                    self._session_settings = settings
                    self._remember()
            if unsent:
                yield deepcopy(self._display), self._status(i18n('model.openai_agent.unsent'))
                return
            for message in worker_messages(command):
                with self._lock:
                    if not self._accept(message, generation):
                        return
                    detail = str(message.get('message') or message.get('progress') or '')[:1000]
                    if message.get('type') == 'error':
                        if self._state.get('outcome') not in TERMINAL:
                            self._state['outcome'] = 'incomplete' if self._state.get('session_id') else 'uncertain'
                        terminal = True
                    elif message.get('type') == 'result':
                        terminal = self._state.get('outcome') in TERMINAL
                    cancel = self._cancel_requested
                    update = deepcopy(self._display), self._status(detail)
                if cancel:
                    self._cancel(generation)
                yield update
                if terminal:
                    break
            if self._state.get('outcome') == 'completed' and self._state.get('session_id'):
                self._download(generation)
                yield deepcopy(self._display), self._status()
        finally:
            with self._lock:
                if self._state.get('generation') == generation:
                    if not started:
                        self._state, self.history, self._display, self._answer_index, self._answer_row = previous
                    elif not terminal:
                        self._state['outcome'] = 'incomplete' if self._state.get('session_id') else 'uncertain'
                    self._running = False
                    self.chatbot = deepcopy(self._display)
                    self._remember()
                    if started:
                        self.auto_save(self.chatbot)
        if not terminal:
            yield deepcopy(self._display), self._status(detail or i18n('model.openai_agent.reconcile_first'))

    def retry(self, chatbot, *args, **kwargs):
        # Regenerate is reconciliation, never another billed input or local rewind.
        with self._lock:
            if self._running or self._retired:
                raise gr.Error(i18n('model.openai_agent.reconcile_first'))
            generation = self._state.get('generation')
            session = self._state.get('session_id')
            nothing_to_recover = self._state.get('outcome') == 'not_started' or (not session and self._state.get('outcome') != 'uncertain')
            self._running = not nothing_to_recover
            command = {'action': 'inspect' if session else 'recover_unknown', 'session_id': session,
                       'turn_id': self._state.get('turn_id'), 'run_id': generation,
                       'baseline_turn_ids': self._state.get('baseline_turn_ids'),
                       'submission_started': self._state.get('submission_started') is True}
        if nothing_to_recover:
            yield gr.update(), self._status(i18n('model.openai_agent.retry_readonly'))
            return
        try:
            for message in worker_messages(command):
                with self._lock:
                    if message.get('type') == 'error':
                        # A failed read cannot resolve the earlier submission.
                        message = {key: value for key, value in message.items()
                                   if key not in ('outcome', 'baseline_turn_ids', 'submission_started')}
                    if not self._accept(message, generation):
                        return
                    detail = message.get('message') or i18n('model.openai_agent.retry_readonly')
                    update = deepcopy(self._display), self._status(detail)
                    cancel = self._cancel_requested
                if cancel:
                    self._cancel(generation)
                yield update
            if self._state.get('outcome') == 'completed':
                # Replace prior file bubbles, do not add duplicates on repeated recovery.
                self._display = [row for row in self._display if row[0] is not None]
                self._answer_row = len(self._display) - 1 if self._answer_index is not None else None
                self._download(generation)
                yield deepcopy(self._display), self._status(i18n('model.openai_agent.retry_readonly'))
        finally:
            with self._lock:
                self._running = False
                self.chatbot = deepcopy(self._display)
                self._remember()
                self.auto_save(self.chatbot)

    def auto_save(self, chatbot=None):
        with self._lock:
            super().auto_save(chatbot)
            self._remember()

    def load_chat_history(self, new_history_file_path=None):
        with self._lock:
            self._assert_idle()
            self._remember()
            result = list(super().load_chat_history(new_history_file_path))
            self.metadata = {}
            self.stream = True
            self.history = [dict(role=row['role'], content=row['content']) for row in self.history
                            if isinstance(row, dict) and row.get('role') in ('user', 'assistant') and isinstance(row.get('content'), str)]
            self.chatbot = [[self.history[index]['content'], self.history[index + 1]['content']]
                            for index in range(0, len(self.history) - 1, 2)]
            self._display = deepcopy(self.chatbot)
            self._restore_binding()
            result[1] = self.system_prompt
            result[2] = gr.update(value=self.chatbot)
            result[14] = True
            return tuple(result)

    def upload_chat_history(self, new_history_file_content=None):
        with self._lock:
            self._assert_idle()
            self._remember()
            self._importing = True
            try:
                return super().upload_chat_history(new_history_file_content)
            finally:
                self._importing = False

    def reset(self, remain_system_prompt=False):
        with self._lock:
            self._assert_idle()
            self._remember()  # Completed sessions remain recoverable in the private journal.
            result = list(super().reset(remain_system_prompt))
            if not remain_system_prompt:
                self.system_prompt = self._default_instructions
            self.stream = True
            result[3] = self.system_prompt
            result[15] = True
            self._restore_binding()
            self._display = []
            self.chatbot = []
            return tuple(result)

    def rename_chat_history(self, filename):
        with self._lock:
            self._assert_idle()
            old_key = self._key()
            result = super().rename_chat_history(filename)
            self._remember()
            if self._key() != old_key:
                with _bindings_lock:
                    _bindings.pop(old_key, None)
            return result

    def delete_chat_history(self, filename):
        with self._lock:
            self._assert_idle()
            self._remember()
        return super().delete_chat_history(filename)

    def delete_first_conversation(self):
        raise gr.Error(i18n('model.openai_agent.no_rewind'))

    def delete_last_conversation(self, chatbot):
        raise gr.Error(i18n('model.openai_agent.no_rewind'))

    def auto_name_chat_history(self, *args):
        return gr.update()  # Never invoke an ordinary model to name an Agent task.

    def handle_file_upload(self, *args):
        raise gr.Error(i18n('model.openai_agent.no_local_tools'))

    def summarize_index(self, *args):
        raise gr.Error(i18n('model.openai_agent.no_local_tools'))
