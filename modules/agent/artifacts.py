"""在流式执行期间独立读取已发布文件，不阻塞文字事件。"""
from copy import deepcopy
from pathlib import PurePosixPath
import re
from threading import Event, Lock, Thread

from modules.agent.runtime import download_artifacts


def preparing_artifacts(records, *, session_id, turn_id):
    """Published metadata is a disabled card, never evidence of local bytes."""
    if not session_id or not turn_id or not isinstance(records, list): return []
    result = {}
    for record in records:
        if (not isinstance(record, dict) or not isinstance(record.get('id'), str) or not record['id']
                or record.get('session_id') != session_id or record.get('turn_id') != turn_id):
            continue
        source = record.get('name') or record.get('filename') or record.get('path')
        if not isinstance(source, str) or not source: continue
        name = PurePosixPath(source.replace('\\', '/')).name
        name = re.sub(r'[\x00-\x1f\x7f]', '_', name).lstrip('.') or 'artifact'
        size = record.get('size_bytes', record.get('size'))
        pending = dict(id=record['id'], session_id=session_id, turn_id=turn_id, name=name,
                       size=size if type(size) is int and size >= 0 else None, status='preparing')
        remote = record.get('remote_path') or record.get('path') or record.get('filename')
        if isinstance(remote, str): pending['remote_path'] = remote
        if isinstance(record.get('message_item_id'), str): pending['message_item_id'] = record['message_item_id']
        result[pending['id']] = pending
    return list(result.values())


class ArtifactObserver:
    def __init__(self, client, emit, *, enabled=True, skip_artifact_ids=(), interval=1.0, cache_root=None):
        self.client = client.with_options(timeout=5.0, max_retries=0) if enabled and hasattr(client, 'with_options') else client
        if enabled and hasattr(self.client, '__dict__'):
            self.client._agent_read_retries = 0
        self.emit = emit
        self.cache_root = cache_root
        self.enabled, self.interval = enabled, interval
        self.session_id = self.turn_id = None
        self.ready = set(skip_artifact_ids)
        self.stop = Event()
        self.thread = None
        self.error_reported = False
        self._records = {}
        self._records_lock = Lock()

    def snapshot(self, session_id, turn_id):
        # Short in-memory handoff only: never wait on list/content or emit.
        with self._records_lock:
            return deepcopy([record for record in self._records.values()
                             if record.get('session_id') == session_id and record.get('turn_id') == turn_id])

    def __enter__(self):
        return self

    def observe_session(self, session_id, turn_id=None):
        if not self.enabled or not session_id or not turn_id or self.thread is not None:
            return
        self.session_id, self.turn_id = session_id, turn_id
        self.thread = Thread(target=self._watch, daemon=True)
        self.thread.start()

    def _publish(self, records):
        if self.stop.is_set(): return
        with self._records_lock:
            for record in records:
                if isinstance(record.get('id'), str): self._records[record['id']] = deepcopy(record)
            self.ready.update(record['id'] for record in records if record.get('status') in ('ready', 'failed'))
        self.emit('progress', session_id=self.session_id, artifacts=records)

    def _refresh(self):
        try:
            download_artifacts(self.client, self.session_id, turn_id=self.turn_id,
                               skip_artifact_ids=self.ready, on_progress=self._publish, cache_root=self.cache_root, should_cancel=self.stop.is_set, live=True)
            self.error_reported = False
        except Exception:
            if not self.error_reported and not self.stop.is_set():
                self.emit('progress', session_id=self.session_id,
                          artifact_error='文件列表暂时无法读取；回答不受影响，可稍后重试')
                self.error_reported = True

    def _watch(self):
        while not self.stop.is_set():
            self._refresh()
            self.stop.wait(self.interval)

    def __exit__(self, *args):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=0.5)
