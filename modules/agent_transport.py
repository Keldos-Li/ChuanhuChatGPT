"""Bounded JSON-lines transport to an isolated official SDK worker."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from modules import shared
from modules.presets import i18n

ROOT = Path(shared.chuanhu_path).resolve()

def worker_messages(command):
    executable = os.environ.get('CHUANHU_AGENT_PYTHON') or str(ROOT / '.agents-runtime' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))
    if not Path(executable).is_file():
        yield {'type': 'error', 'outcome': 'not_started', 'message': i18n('model.openai_agent.runtime_missing')}
        return
    environment = dict(os.environ)
    for name in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_API_BASE', 'OPENAI_ORG_ID', 'OPENAI_PROJECT_ID', 'OPENAI_LOG'):
        environment.pop(name, None)
    process = subprocess.Popen([executable, '-u', str(ROOT / 'optional/agents/worker.py')],
                               cwd=ROOT, env=environment, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding='utf-8')
    messages = queue.Queue(maxsize=64)
    stopped = threading.Event()
    def enqueue(value):
        while not stopped.is_set():
            try:
                messages.put(value, timeout=1)
                return
            except queue.Full:
                pass
    def reader():
        try:
            while not stopped.is_set():
                line = process.stdout.readline(1000001)
                if not line:
                    break
                if len(line) > 1000000:
                    enqueue(json.dumps({'type': 'error', 'message': i18n('model.openai_agent.invalid_response')}))
                    break
                enqueue(line)
        except (OSError, ValueError):
            pass
        finally:
            enqueue(None)
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        process.stdin.write(json.dumps(command, ensure_ascii=False) + '\n')
        process.stdin.close()
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            try:
                line = messages.get(timeout=1)
            except queue.Empty:
                continue
            if line is None:
                break
            try:
                message = json.loads(line)
            except ValueError:
                yield {'type': 'error', 'message': i18n('model.openai_agent.invalid_response')}
                break
            if not isinstance(message, dict) or message.get('type') not in ('progress', 'result', 'error'):
                yield {'type': 'error', 'message': i18n('model.openai_agent.invalid_response')}
                break
            yield message
        else:
            yield {'type': 'error', 'message': i18n('model.openai_agent.timeout')}
    finally:
        stopped.set()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        process.stdout.close()
        thread.join(timeout=1)
