import hashlib
import json
import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Any


class Cache:
    """Small dependency-free SQLite cache suitable for a single service process."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "cache.sqlite3"
        self._lock = Lock()
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS cache "
                "(namespace TEXT, key TEXT, value BLOB, expires REAL, PRIMARY KEY(namespace,key))"
            )

    def _connect(self):
        return sqlite3.connect(self.path)

    @staticmethod
    def key(data: Any) -> str:
        encoded = json.dumps(data, sort_keys=True, default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

    def get(self, namespace: str, key: str) -> Any | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT value, expires FROM cache WHERE namespace=? AND key=?",
                (namespace, key),
            ).fetchone()
            if not row:
                return None
            if row[1] < time.time():
                db.execute("DELETE FROM cache WHERE namespace=? AND key=?", (namespace, key))
                return None
            return json.loads(row[0])

    def set(self, namespace: str, key: str, value: Any, ttl: int = 3600) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO cache VALUES (?,?,?,?)",
                (namespace, key, json.dumps(value, default=str), time.time() + ttl),
            )
