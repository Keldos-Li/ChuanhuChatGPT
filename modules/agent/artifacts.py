"""在流式执行期间独立读取已发布文件，不阻塞文字事件。"""
from threading import Event, Thread

from modules.agent.runtime import download_artifacts


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
