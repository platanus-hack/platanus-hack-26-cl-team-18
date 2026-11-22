"""A local JSONL file for write-behind units Postgres could not take (money must not vanish).

`WriteBehindWriter` spills a durable unit here instead of dropping it: when both its
queue and overflow are full, or at shutdown with the database unreachable. The next
process start replays the file (`replay_spill`) before serving, one transaction per
unit. Replays are safe to repeat because every spilled statement is an append with
ON CONFLICT DO NOTHING on its natural key, so a unit written twice (spilled while a
dying writer thread also wrote it) lands once.

The file is created only when something spills. One line per unit:
`{"statements": [[sql, [param, ...]], ...]}`. Params keep their types through tagged
values (`{"$decimal": "1.50"}`, `{"$datetime": "..."}`, `{"$uuid": "..."}`); a param
of any other non-JSON type is refused at spill time (the unit is then dropped and
counted, as before). Only callers that pass rows without personal data may opt in
(charges: ids, amounts, instants).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

type Statement = tuple[str, Sequence[Any]]
type Unit = tuple[Statement, ...]


def _encode(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, UUID):
        return {"$uuid": str(value)}
    if isinstance(value, list | tuple):
        return [_encode(item) for item in value]
    raise TypeError(f"cannot spill a {type(value).__name__} parameter")


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if isinstance(value, dict) and len(value) == 1:
        ((tag, raw),) = value.items()
        if tag == "$decimal":
            return Decimal(raw)
        if tag == "$datetime":
            return datetime.fromisoformat(raw)
        if tag == "$date":
            return date.fromisoformat(raw)
        if tag == "$uuid":
            return UUID(raw)
    return value


def encode_unit(unit: Unit) -> str:
    return json.dumps(
        {"statements": [[sql, [_encode(p) for p in params]] for sql, params in unit]},
        separators=(",", ":"),
    )


def decode_unit(line: str) -> Unit:
    data = json.loads(line)
    return tuple((str(sql), tuple(_decode(p) for p in params)) for sql, params in data["statements"])


class SpillFile:
    """Append-only, fsynced JSONL of units; thread safe within one process."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def append(self, units: Sequence[Unit]) -> int:
        """Spill `units`; returns the statements written (a unit with an unknown param
        type is skipped and logged)."""
        lines = []
        rows = 0
        for unit in units:
            try:
                lines.append(encode_unit(unit))
            except TypeError as exc:
                logger.error("spill %s refused a unit: %s", self.path, exc)
                continue
            rows += len(unit)
        if not lines:
            return 0
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write("".join(f"{line}\n" for line in lines))
                handle.flush()
                os.fsync(handle.fileno())
        return rows

    def read(self) -> list[Unit]:
        """Every complete unit on disk (a torn last line from a crash is skipped)."""
        with self._lock:
            if not self.path.is_file():
                return []
            text = self.path.read_text(encoding="utf-8")
        units = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                units.append(decode_unit(line))
            except ValueError, KeyError, TypeError:
                logger.error("spill %s: line %d is unreadable, skipped", self.path, number)
        return units

    def replace(self, units: Sequence[Unit]) -> None:
        """Keep only `units` (what a partial replay could not write); none removes the file."""
        with self._lock:
            if not units:
                self.path.unlink(missing_ok=True)
                return
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                handle.write("".join(f"{encode_unit(unit)}\n" for unit in units))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)


def _default_transient(exc: BaseException) -> bool:
    return isinstance(exc, ConnectionError | TimeoutError | OSError)


def replay_spill(
    spill: SpillFile,
    connect: Callable[[], Any],
    *,
    transient: Callable[[BaseException], bool] = _default_transient,
) -> int:
    """Write every spilled unit, one transaction each; returns how many were written.

    A unit the database refuses (bad data) is skipped and logged, so one row can't block
    the rest forever. If the database is unreachable, the remaining units stay on disk
    for the next start; the station still starts (charges stay in memory meanwhile).
    """
    units = spill.read()
    if not units:
        return 0
    written = 0
    conn: Any = None
    try:
        conn = connect()
        for index, unit in enumerate(units):
            try:
                with conn, conn.cursor() as cur:
                    for sql, params in unit:
                        cur.execute(sql, params)
                written += 1
            except Exception as exc:
                if transient(exc):
                    spill.replace(units[index:])
                    logger.warning(
                        "spill replay stopped (%s): %d unit(s) kept in %s",
                        type(exc).__name__,
                        len(units) - index,
                        spill.path,
                    )
                    return written
                logger.error("spill replay refused a unit (%s): skipped", type(exc).__name__)
    except Exception as exc:
        logger.warning(
            "spill replay could not reach the database (%s): %d unit(s) kept in %s",
            type(exc).__name__,
            len(units),
            spill.path,
        )
        return written
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover - best effort
                pass
    spill.replace([])
    logger.warning("spill replay wrote %d unit(s) from %s", written, spill.path)
    return written
