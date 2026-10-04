"""Bounded Agent execution owned by one server process, independent of UI readers.

No frame backlog or browser-owned worker: subscribers read only the newest
snapshot. Durable bindings remain authoritative after this process exits.
"""
from copy import deepcopy
from pathlib import Path
from threading import Condition, RLock, Thread
import time

import gradio as gr

# Only conversation data crosses into a new UI view. Credentials, upload stager,
# locks, pending submission tokens and UI epochs never cross this boundary.
PROJECTION = (
    '_background_busy', 'history', 'chatbot', '_display', '_state', '_artifacts', '_cloud_items',
    '_pending_actions', '_session_settings', '_needs_sync', '_notice',
    '_unavailable', '_connection_mismatch', '_active_input_cards',
    '_draft_submitted', '_draft_acknowledged', '_first_prompt', '_auto_named',
    '_answer_index', '_answer_row', '_input_messages', '_installed_inputs',
    '_input_context', '_input_statuses', '_input_preparing', '_reasoning',
    'model_name', 'system_prompt', '_tool_settings', '_pending_model_settings',
)
TERMINAL = {'not_started', 'completed', 'cancelled', 'failed'}


class BackgroundTask:
    def __init__(self, registry, model, execute):
        self.registry, self.model, self.execute = registry, model, execute
        self.owner, self.conversation = model._owner, model._conversation_id
        self.key = registry.key(model)
        self.path = registry.path(model)
        self.condition = Condition()
        self.revision = 0
        self.snapshot = None
        self.generation = None
        self.done = False
        self.error = None
        self.thread = Thread(target=self._run, name='agent-background', daemon=True)

    def publish(self):
        with self.model._lock:
            generation = self.model._state.get('generation')
            if self.generation is None: self.generation = generation
            if getattr(self.model, '_background_task', None) is not self:
                raise RuntimeError('后台任务所有权已变化，拒绝发布其他任务的快照')
            values = {name: deepcopy(getattr(self.model, name)) for name in PROJECTION if hasattr(self.model, name)}
            values['_running'] = not self.done
            status = self.model._status()
            session = self.model._state.get('session_id')
        if not self.done: self.registry.bind_session(self, session)
        with self.condition:
            self.snapshot = (values, status)
            self.revision += 1
            self.condition.notify_all()

    def _run(self):
        try:
            for _ in self.execute():
                self.publish()
        except Exception as error:
            self.error = error
            with self.model._lock:
                if self.model._state.get('outcome') not in TERMINAL:
                    self.model._state['outcome'] = 'incomplete' if self.model._state.get('session_id') else 'uncertain'
                self.model._notice = getattr(error, 'message', str(error))
                self.model._running = False
                self.model.chatbot = deepcopy(self.model._display)
                try:
                    self.model._remember()
                    if self.model.history: self.model.auto_save(self.model.chatbot)
                except Exception:
                    import logging
                    logging.exception('后台任务保存失败，原始错误及已有记录保留')
        finally:
            # Hold subscribers until the immutable final snapshot is captured
            # AND the registry reservation is released. A new generation can
            # never be read into the old task's final frame.
            with self.condition:
                self.done = True
                self.model._background_busy = False
                self.model._task_phase = "settled"
                try:
                    try:
                        with self.model._lock: self.model._remember()
                    except Exception as error:
                        if self.error is None: self.error = error
                        with self.model._lock:
                            self.model._notice = getattr(error, 'message', str(error))
                        import logging
                        logging.exception('后台任务最终保存失败，已有记录保留并向观察者报告')
                    self.publish()
                finally:
                    self.registry.finish(self)
                    self.condition.notify_all()

    def project(self, view):
        with self.condition:
            snapshot = self.snapshot
        if snapshot is None: return
        values, _ = snapshot
        with view._lock:
            if view._owner != self.owner: raise gr.Error("此任务不属于当前登录用户")
            existing = getattr(view, '_background_task', None)
            if existing is not None and existing is not self: return
            view._conversation_id = self.conversation
            if view is not self.model:
                for name, value in values.items(): setattr(view, name, deepcopy(value))

    def subscribe(self, view):
        """Closing this iterator has no effect on the task or its transport."""
        revision = -1
        visit = view.agent_choice_target
        while True:
            if view._retired or view.agent_choice_target != visit: return
            with self.condition:
                ready = self.condition.wait_for(lambda: self.revision != revision or self.done, timeout=.25)
                if not ready: continue
                revision = self.revision
                snapshot, done, error = self.snapshot, self.done, self.error
            if snapshot is not None:
                values, status = snapshot
                with view._lock:
                    if (view._conversation_id != self.conversation or view._owner != self.owner
                            or getattr(view, '_background_task', self) is not self):
                        return
                    if view is not self.model:
                        for name, value in values.items(): setattr(view, name, deepcopy(value))
                    chat = deepcopy(values['_display'])
                yield chat, status
            if done:
                if error is not None: raise error
                return

    def stop(self):
        # This exact model cannot be reset/load by UI callbacks. interrupt captures
        # generation/session/turn under its lock; it never reads a current page.
        return self.model.interrupt(expected_task=self, expected_generation=self.generation)


class TaskRegistry:
    def __init__(self, *, limit=8, owner_limit=3):
        self.limit, self.owner_limit = limit, owner_limit
        self.lock = RLock()
        self.tasks = {}
        self.sessions = {}

    @staticmethod
    def root(model):
        from modules import shared
        return getattr(model, "_task_root", str(Path(shared.chuanhu_path).resolve()))

    def key(self, model): return self.root(model), model._owner, model._conversation_id

    def path(self, model):
        # Normalize basename and absolute load paths identically.
        from modules.models.base_model import HISTORY_DIR
        name = Path(model.history_file_path)
        if not name.is_absolute(): name = Path(HISTORY_DIR) / model.user_name / name
        return str(name.resolve()).removesuffix('.json')

    def find_conversation(self, username, conversation):
        from modules.agent.store import owner_identity
        from modules import shared
        key = (str(Path(shared.chuanhu_path).resolve()), owner_identity(username), conversation)
        with self.lock: return self.tasks.get(key)

    def find_history(self, username, path):
        from modules.agent.store import owner_identity
        from modules import shared
        from modules.models.base_model import HISTORY_DIR
        name = Path(path)
        if not name.is_absolute(): name = Path(HISTORY_DIR) / username / name
        target = str(name.resolve()).removesuffix('.json')
        root, owner = str(Path(shared.chuanhu_path).resolve()), owner_identity(username)
        with self.lock:
            return next((task for (r, o, _), task in self.tasks.items()
                         if r == root and o == owner and task.path == target), None)

    def find(self, model, path=None):
        root, owner = self.root(model), model._owner
        with self.lock:
            if path is None: return self.tasks.get(self.key(model))
            name = Path(path)
            if not name.is_absolute():
                from modules.models.base_model import HISTORY_DIR
                name = Path(HISTORY_DIR) / model.user_name / name
            target = str(name.resolve()).removesuffix('.json')
            return next((task for (r, o, _), task in self.tasks.items()
                         if r == root and o == owner and task.path == target), None)

    def start(self, model, execute):
        with self.lock:
            key = self.key(model)
            session = model._state.get('session_id')
            session_key = (*key[:2], session)
            if key in self.tasks or (session and session_key in self.sessions):
                raise gr.Error('此对话已有后台任务，请等待完成或停止该任务')
            if len(self.tasks) >= self.limit or sum(k[:2] == key[:2] for k in self.tasks) >= self.owner_limit:
                raise gr.Error('后台任务数量已达上限，本次消息尚未提交，请等待一个任务结束')
            task = BackgroundTask(self, model, execute)
            self.tasks[key] = task
            if session: self.sessions[session_key] = task
            previous = {name: (hasattr(model, name), getattr(model, name, None)) for name in ('_task_root', '_background_task', '_task_backend', '_background_busy', '_task_phase')}
            model._task_root = key[0]
            model._background_task = task
            model._task_backend = True
            model._background_busy = True
            model._task_phase = "preparing"
            try:
                task.thread.start()
            except Exception:
                self.tasks.pop(key, None)
                for alias in [k for k, value in self.sessions.items() if value is task]: self.sessions.pop(alias)
                for name, (existed, value) in previous.items():
                    if existed: setattr(model, name, value)
                    else: delattr(model, name)
                raise
            return task

    def bind_session(self, task, session):
        if not session: return
        with self.lock:
            key = (*task.key[:2], session)
            existing = self.sessions.get(key)
            if existing is not None and existing is not task:
                raise gr.Error('此云端会话已有后台任务，保留现有记录并停止重复观察')
            self.sessions[key] = task

    def finish(self, task):
        # Uncertain outcomes are durable and blocked by _assert_idle after the
        # thread ends; no unbounded dormant entries are needed for that lock.
        with self.lock:
            key = task.key
            if self.tasks.get(key) is task: self.tasks.pop(key)
            for alias in [key for key, value in self.sessions.items() if value is task]:
                self.sessions.pop(alias)


TASKS = TaskRegistry()
