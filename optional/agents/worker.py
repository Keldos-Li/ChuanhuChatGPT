"""JSON-lines worker using the main application's interpreter and configuration."""
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from optional.agents.connection import AgentConnectionError
from optional.agents.tools import ToolConfigurationError
from runtime import (AgentError, create_client, run_task, inspect_saved, recover_stream,
                     cancel_session, download_artifacts, find_uncertain_session, update_settings,
                     submit_browser_response, safe_request_error)


def emit(kind, **data):
    print(json.dumps({'type': kind, **data}, ensure_ascii=False), flush=True)


def main():
    state, action = None, None
    try:
        command = json.loads(sys.stdin.readline())
        action = command.get('action')
        allowed = {'run', 'inspect', 'recover', 'recover_unknown', 'cancel', 'download', 'update', 'browser_response', 'capabilities', 'title'}
        if action not in allowed: raise AgentError('未知 Agent 操作')
        session_id = command.get('session_id')
        if session_id and not re.fullmatch(r'sess_[A-Za-z0-9_-]+', session_id): raise AgentError('无效会话标识')
        if action not in ('run', 'recover_unknown', 'capabilities', 'title') and not session_id: raise AgentError('当前没有可管理的会话')
        with create_client(command.get('connection')) as client:
            if action == 'capabilities':
                import openai
                emit('result', sdk_version=openai.__version__, available=hasattr(client.beta, 'agents'))
                return
            def progress(value):
                nonlocal state
                state = value
                emit('progress', **value.snapshot())
            if action == 'title':
                result = client.chat.completions.create(model=command['model'], messages=[
                    {'role': 'system', 'content': 'Write a short title for this conversation. Return only the title.'},
                    {'role': 'user', 'content': json.dumps(command.get('history', []), ensure_ascii=False)}])
                emit('result', title=result.choices[0].message.content or '')
            elif action == 'run':
                state = run_task(client, command.get('prompt'), command.get('model'), session_id=session_id,
                                 run_id=command.get('run_id'), on_progress=progress, instructions=command.get('instructions'),
                                 reasoning=command.get('reasoning'), tool_settings=command.get('tool_settings'),
                                 history_reference=command.get('history_reference'))
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
        emit('error', message=str(sanitized), diagnostics=getattr(sanitized, 'diagnostics', {}),
             **(snapshot.snapshot() if snapshot else {'outcome': 'not_started' if action == 'run' else 'incomplete'}))


if __name__ == '__main__': main()
