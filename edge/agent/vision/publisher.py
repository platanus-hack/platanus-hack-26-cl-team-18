"""Latest-wins WebSocket publication, independent of video inference."""

from __future__ import annotations

import json
import queue
import threading
from urllib.parse import urlparse


class SnapshotPublisher:
    def __init__(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise ValueError("Backend URL must be a WebSocket URL")
        if parsed.scheme == "ws" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Non-local WebSocket connections require TLS (wss)")
        self.url = url
        self.pending: queue.Queue[str] = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._send, daemon=True)
        self.thread.start()

    def publish(self, snapshot: dict) -> None:
        payload = json.dumps(snapshot, allow_nan=False)
        try:
            self.pending.put_nowait(payload)
        except queue.Full:
            try:
                self.pending.get_nowait()
            except queue.Empty:
                pass
            self.pending.put_nowait(payload)

    def _send(self) -> None:
        from websockets.sync.client import connect

        connection = None
        try:
            while not self.stop.is_set() or not self.pending.empty():
                try:
                    payload = self.pending.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    if connection is None:
                        connection = connect(self.url, open_timeout=1, close_timeout=1)
                    connection.send(payload)
                except Exception:
                    if connection is not None:
                        try:
                            connection.close()
                        except Exception:
                            pass
                        connection = None
                    # The next fresh snapshot retries; old observations are discarded.
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=2)
