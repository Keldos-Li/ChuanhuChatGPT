"""Server-private Agent bindings. Exported chat JSON never confers authority."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3


def owner_identity(username):
    # Anonymous deployments already share one history boundary. Do not invent a
    # browser-only identity that disappears on refresh or a server restart.
    return hashlib.sha256(('user:' + username if username else 'deployment:anonymous').encode()).hexdigest()


def history_key(path):
    return str(path).removesuffix('.json')


class BindingStore:
    def __init__(self, root):
        folder = Path(root) / 'agent_data'
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = folder / 'bindings.sqlite3'
        if self.path.is_symlink():
            raise ValueError('会话绑定文件不能是符号链接')
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS bindings (owner TEXT NOT NULL, history TEXT NOT NULL, record TEXT NOT NULL, PRIMARY KEY(owner, history))')
        os.chmod(self.path, 0o600)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def get(self, owner, path):
        with self._connect() as db:
            row = db.execute('SELECT record FROM bindings WHERE owner=? AND history=?', (owner, history_key(path))).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, owner, path, record):
        # Caller supplies no credentials. Reject accidental secret fields rather
        # than exporting or silently stripping a connection authority record.
        def check(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.lower() in {'api_key', 'authorization', 'password', 'access_token', 'credential_values'}:
                        raise ValueError('不能将凭据保存在会话绑定中')
                    check(item)
            elif isinstance(value, list):
                for item in value: check(item)
        check(record)
        with self._connect() as db:
            db.execute('INSERT OR REPLACE INTO bindings VALUES (?, ?, ?)', (owner, history_key(path), json.dumps(record, ensure_ascii=False)))

    def copy_binding(self, owner, before, after):
        """Retain source authority until the history file migration completes."""
        with self._connect() as db:
            db.execute('INSERT INTO bindings SELECT owner, ?, record FROM bindings WHERE owner=? AND history=?',
                       (history_key(after), owner, history_key(before)))

    def rename(self, owner, before, after):
        with self._connect() as db:
            db.execute('UPDATE bindings SET history=? WHERE owner=? AND history=?', (history_key(after), owner, history_key(before)))

    def forget(self, owner, path):
        with self._connect() as db:
            db.execute('DELETE FROM bindings WHERE owner=? AND history=?', (owner, history_key(path)))
