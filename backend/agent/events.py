"""In-process event bus feeding the SSE stream: one queue per subscriber, per project."""
from __future__ import annotations

import queue
import threading
from typing import Any

_subs: dict[str, list[queue.Queue]] = {}
_lock = threading.Lock()


def subscribe(project_id: str) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=2000)
    with _lock:
        _subs.setdefault(project_id, []).append(q)
    return q


def unsubscribe(project_id: str, q: queue.Queue) -> None:
    with _lock:
        if q in _subs.get(project_id, []):
            _subs[project_id].remove(q)


def publish(project_id: str, event: str, data: Any) -> None:
    with _lock:
        subs = list(_subs.get(project_id, []))
    for q in subs:
        try:
            q.put_nowait((event, data))
        except queue.Full:
            pass


if __name__ == "__main__":
    q = subscribe("p")
    publish("p", "log", {"text": "hi"})
    assert q.get_nowait() == ("log", {"text": "hi"})
    unsubscribe("p", q)
    print("agent.events self-check OK")
