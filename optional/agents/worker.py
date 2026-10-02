"""JSON-lines worker using the main application's interpreter and configuration."""
import json
from copy import deepcopy
import os
import re
import stat
import sys
from pathlib import Path
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from optional.agents.connection import AgentConnectionError
from optional.agents.tools import ToolConfigurationError
from optional.agents.inputs import prepare_inputs
from optional.agents.runtime import (AgentError, create_client, run_task, inspect_saved, recover_stream,
                     cancel_session, download_artifacts, find_uncertain_session, update_settings,
                     submit_browser_response, safe_request_error)


def emit(kind, **data):
    print(json.dumps({'type': kind, **data}, ensure_ascii=False), flush=True)


class PreparationBusy(AgentError):
    preparation_busy = True


class PreparationJournal:
    """Atomic receipts and cancellation in the server-owned staging directory.

    Pin every directory component without following symlinks. No browser or
    saved-history path may be used to construct this journal; the parent worker
    command must use its authenticated conversation's staging root.
    """
    def __init__(self, staging_root, run_id):
        import fcntl
        self.directory, self.lock = None, None
        if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{32}', run_id):
            raise AgentError('附件准备任务标识无效')
        if not isinstance(staging_root, (str, os.PathLike)):
            raise AgentError('附件准备缺少可信暂存目录')
        path = Path(staging_root)
        if not path.is_absolute() or '..' in path.parts or not hasattr(os, 'O_NOFOLLOW'):
            raise AgentError('附件准备暂存目录无效')
        self.name, self.cancel_name = '.prepare-' + run_id + '.json', '.cancel-' + run_id
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(path.anchor, flags)
        try:
            for part in path.parts[1:]:
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            self.directory = descriptor
        except BaseException:
            os.close(descriptor)
            raise
        try:
            self.lock = os.open('.prepare-' + run_id + '.lock',
                                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                0o600, dir_fd=self.directory)
            if not stat.S_ISREG(os.fstat(self.lock).st_mode):
                raise AgentError('附件准备锁文件无效')
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                try:
                    previous = self.read()
                except Exception:
                    previous = None
                raise PreparationBusy('附件仍在后台准备，消息未发送', previous) from None
        except BaseException:
            self.close()
            raise

    def read(self):
        try:
            descriptor = os.open(self.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=self.directory)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, 'r', encoding='utf-8') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise AgentError('附件准备记录必须为普通文件')
            value = json.load(stream)
        if not isinstance(value, dict):
            raise AgentError('附件准备记录无效')
        return value

    def write(self, value):
        temporary = self.name + '.' + uuid4().hex + '.tmp'
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.directory)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.name, src_dir_fd=self.directory, dst_dir_fd=self.directory)
            os.fsync(self.directory)
        except BaseException:
            try:
                os.unlink(temporary, dir_fd=self.directory)
            except FileNotFoundError:
                pass
            raise

    def cancelled(self):
        try:
            os.stat(self.cancel_name, dir_fd=self.directory, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def close(self):
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None
        if self.directory is not None:
            os.close(self.directory)
            self.directory = None


def _empty_preparation(command):
    if isinstance(command.get('resume_state'), dict):
        return deepcopy(command['resume_state'])
    return {'session_id': command.get('session_id'), 'environment_id': None,
            'run_id': command.get('run_id'), 'outcome': 'failed', 'submission_started': False,
            'baseline_turn_ids': [], 'baseline_verified': False, 'files': [],
            'installed': deepcopy(command.get('installed') or {}),
            'uncertain_operation': None, 'session_creation_started': False}


def main():
    state, action, journal = None, None, None
    command, output_closed = {}, False
    def preparation_emit(kind, **data):
        nonlocal output_closed
        if output_closed:
            return
        try:
            emit(kind, **data)
        except BrokenPipeError:
            # The parent/browser may disconnect; keep recording remote results.
            output_closed = True
            try:
                descriptor = os.open(os.devnull, os.O_WRONLY)
                try:
                    os.dup2(descriptor, sys.stdout.fileno())
                finally:
                    os.close(descriptor)
            except (AttributeError, OSError, ValueError):
                pass

    try:
        command = json.loads(sys.stdin.readline())
        action = command.get('action')
        allowed = {'run', 'prepare_inputs', 'inspect', 'recover', 'recover_unknown', 'cancel', 'download', 'update', 'browser_response', 'capabilities', 'title'}
        if action not in allowed: raise AgentError('未知 Agent 操作')
        session_id = command.get('session_id')
        if session_id and not re.fullmatch(r'sess_[A-Za-z0-9_-]+', session_id): raise AgentError('无效会话标识')
        if action not in ('run', 'prepare_inputs', 'recover_unknown', 'capabilities', 'title') and not session_id: raise AgentError('当前没有可管理的会话')
        if action == 'prepare_inputs':
            journal = PreparationJournal(command.get('staging_root'), command.get('run_id'))
            # Read after acquiring the lock: another worker may have advanced
            # beyond the parent's last observed receipt before it exited.
            latest = journal.read()
            if latest is not None:
                command['resume_state'] = latest
            state = latest if latest is not None else _empty_preparation(command)
        with create_client(command.get('connection')) as client:
            if action == 'capabilities':
                import openai
                emit('result', sdk_version=openai.__version__, available=hasattr(client.beta, 'agents'))
                return
            def progress(value):
                nonlocal state
                state = value
                emit('progress', **value.snapshot())
            if action == 'prepare_inputs':
                def preparation_progress(value):
                    nonlocal state
                    state = value
                    journal.write(value)
                    preparation_emit('progress', preparation=value)
                state = prepare_inputs(client, command.get('inputs'), command.get('model'),
                    staging_root=command.get('staging_root'), session_id=session_id,
                    run_id=command.get('run_id'), instructions=command.get('instructions'),
                    reasoning=command.get('reasoning'), tool_settings=command.get('tool_settings'),
                    installed=command.get('installed'), resume_state=command.get('resume_state'),
                    on_progress=preparation_progress, should_cancel=journal.cancelled)
                journal.write(state)
                preparation_emit('result', preparation=state)
            elif action == 'title':
                result = client.chat.completions.create(model=command['model'], messages=[
                    {'role': 'system', 'content': 'Write a short title for this conversation. Return only the title.'},
                    {'role': 'user', 'content': json.dumps(command.get('history', []), ensure_ascii=False)}])
                emit('result', title=result.choices[0].message.content or '')
            elif action == 'run':
                state = run_task(client, command.get('prompt'), command.get('model'), session_id=session_id,
                                 run_id=command.get('run_id'), on_progress=progress, instructions=command.get('instructions'),
                                 reasoning=command.get('reasoning'), tool_settings=command.get('tool_settings'),
                                 history_reference=command.get('history_reference'), input_files=command.get('input_files'))
                emit('result', **state.snapshot())
            elif action in ('recover', 'recover_unknown'):
                turn_id = command.get('turn_id')
                if action == 'recover_unknown': session_id, turn_id = find_uncertain_session(client, command.get('run_id'))
                state = recover_stream(client, session_id, turn_id, baseline_turn_ids=command.get('baseline_turn_ids'),
                       submission_started=command.get('submission_started') is True, tool_settings=command.get('tool_settings'), on_progress=progress)
                emit('result', **state.snapshot())
            elif action == 'inspect':
                emit('result', **inspect_saved(client, session_id, command.get('turn_id'), command.get('baseline_turn_ids'), command.get('submission_started') is True))
            elif action == 'cancel': emit('result', session_id=session_id, turn_id=command.get('turn_id'), **cancel_session(client, session_id, command.get('turn_id')))
            elif action == 'download': emit('result', session_id=session_id, artifacts=download_artifacts(client, session_id, artifact_ids=command.get('artifact_ids'), on_progress=lambda records: emit('progress', session_id=session_id, artifacts=records)))
            elif action == 'update': emit('result', session_id=session_id, settings=update_settings(client, session_id, command.get('model'), command.get('reasoning')))
            elif action == 'browser_response':
                # Never echo submitted fields, including in errors or diagnostics.
                result = submit_browser_response(client, session_id, command.get('turn_id'), command.get('request_id'), command.get('response'))
                emit('result', session_id=session_id, **result)
                command.pop('response', None)
    except Exception as error:
        snapshot = getattr(error, 'state', None) or state
        sanitized = error if isinstance(error, (AgentError, AgentConnectionError, ToolConfigurationError)) else safe_request_error(error)
        if action == 'prepare_inputs':
            preparation = snapshot if isinstance(snapshot, dict) else _empty_preparation(command)
            preparation['error'] = str(sanitized)
            if not isinstance(error, PreparationBusy) and preparation.get('outcome') in ('preparing', 'ready'):
                preparation['outcome'] = 'failed'
            if journal:
                try:
                    journal.write(preparation)
                except Exception:
                    # Report failure without pretending a receipt was durable.
                    preparation['journal_saved'] = False
            preparation_emit('error', message=str(sanitized), diagnostics=getattr(sanitized, 'diagnostics', {}),
                             preparation=preparation, **({'preparation_busy': True} if isinstance(error, PreparationBusy) else {}))
        else:
            emit('error', message=str(sanitized), diagnostics=getattr(sanitized, 'diagnostics', {}),
                 **(snapshot.snapshot() if snapshot else {'outcome': 'not_started' if action == 'run' else 'incomplete'}))
    finally:
        if journal:
            journal.close()


if __name__ == '__main__': main()
