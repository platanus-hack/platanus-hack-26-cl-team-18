"""Short-lived recognition read model; never stores frames or audio."""

from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock

from app.contract.messages import RecognitionAttribute, RecognitionPerson, RecognitionSnapshot
from app.realtime.router import Received


def _prefer(candidate: RecognitionAttribute, existing: RecognitionAttribute) -> bool:
    strength = {"low": 3, "medium": 2, "high": 1, "unknown": 0}
    left, right = strength[candidate.uncertainty], strength[existing.uncertainty]
    return left > right or (left == right and candidate.observed_at_s > existing.observed_at_s)


class RecognitionStore:
    def __init__(self, ttl_seconds: float = 30, max_screens: int = 64) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._max_screens = max_screens
        self._items: dict[str, tuple[RecognitionSnapshot, datetime]] = {}
        self._lock = Lock()

    def put(self, message: RecognitionSnapshot, received: Received) -> bool:
        with self._lock:
            current = self._items.get(message.screen_id)
            if (
                current
                and current[0].session_id != message.session_id
                and message.session_started_at <= current[0].session_started_at
            ):
                return False
            if (
                current
                and current[0].session_id == message.session_id
                and current[0].seq >= message.seq
            ):
                return False
            if current and current[0].session_id == message.session_id:
                old_people = {p.track_id: p for p in (*current[0].people, *current[0].history)}

                def preserve(person: RecognitionPerson) -> RecognitionPerson:
                    old = old_people.get(person.track_id)
                    if old is None:
                        return person
                    by_name = {a.name: a for a in person.attributes}
                    for attr in old.attributes:
                        previous = by_name.get(attr.name)
                        if previous is None or _prefer(attr, previous):
                            by_name[attr.name] = attr
                    return person.model_copy(update={"attributes": list(by_name.values())[:64]})

                message = message.model_copy(
                    update={
                        "people": [preserve(p) for p in message.people],
                        "history": [preserve(p) for p in message.history],
                        "revision": max(message.revision, current[0].revision),
                    }
                )
            self._items[message.screen_id] = (message, received.received_at)
            if len(self._items) > self._max_screens:
                oldest = min(self._items, key=lambda key: self._items[key][1])
                del self._items[oldest]
            return True

    def get(self, screen_id: str, now: datetime) -> tuple[RecognitionSnapshot, datetime] | None:
        with self._lock:
            item = self._items.get(screen_id)
            if item is not None and now - item[1] > self._ttl:
                del self._items[screen_id]
                return None
            return item

    def prune(self, now: datetime) -> None:
        with self._lock:
            expired = [
                key for key, (_, received) in self._items.items() if now - received > self._ttl
            ]
            for key in expired:
                del self._items[key]

    def enrich(
        self,
        screen_id: str,
        session_id: str,
        track_id: str,
        observed_at_s: float,
        attributes: list[RecognitionAttribute],
    ) -> bool:
        with self._lock:
            item = self._items.get(screen_id)
            if item is None or item[0].session_id != session_id:
                return False
            snapshot, received_at = item
            found = False

            def merge(person: RecognitionPerson) -> RecognitionPerson:
                nonlocal found
                if (
                    person.track_id != track_id
                    or not person.first_seen_s <= observed_at_s <= person.last_seen_s
                ):
                    return person
                found = True
                by_name = {a.name: a for a in person.attributes}
                for attr in attributes:
                    old = by_name.get(attr.name)
                    if old is None or _prefer(attr, old):
                        by_name[attr.name] = attr
                return person.model_copy(update={"attributes": list(by_name.values())[:64]})

            updated = snapshot.model_copy(
                update={
                    "people": [merge(p) for p in snapshot.people],
                    "history": [merge(p) for p in snapshot.history],
                    "revision": snapshot.revision + 1,
                }
            )
            if found:
                self._items[screen_id] = (updated, received_at)
            return found

    def contains_track(
        self, screen_id: str, session_id: str, track_id: str, observed_at_s: float
    ) -> bool:
        with self._lock:
            item = self._items.get(screen_id)
            return bool(
                item
                and item[0].session_id == session_id
                and any(
                    person.track_id == track_id
                    and person.first_seen_s <= observed_at_s <= person.last_seen_s
                    for person in (*item[0].people, *item[0].history)
                )
            )
