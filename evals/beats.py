"""Score a recording against the demo video's beats (docs/demo-video.md).

`make eval-rehearsal REC=…` scores latency and invariants; this answers the
question a rehearsal is for: *did each beat land on screen, in order?* It reads
the same JSONL the recorder writes (wire messages only, never frames, audio or
transcripts), so a real webcam take and the scripted `eval-demo-video` run are
judged by one scorer.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.contract.messages import (
    ConsoleReceipt,
    ConsoleRedemption,
    CreativeReady,
    CreativeSpec,
    CreativeUpgraded,
)
from evals.live import CONSOLE_MESSAGES, PLAYER_MESSAGES

BASKET = frozenset({"Voltio", "Café Patio"})
RIVAL_BASKET = frozenset({"Rayo", "Café Patio"})


@dataclass(frozen=True, slots=True)
class Event:
    t_s: float
    message: Any


@dataclass(frozen=True, slots=True)
class BeatResult:
    beat: str
    hit: bool
    t_s: float | None
    evidence: str


def _brands(spec: CreativeSpec) -> frozenset[str]:
    return frozenset(slot.brand_name for slot in spec.slots)


def _copy(spec: CreativeSpec) -> str:
    return " ".join([s.copy_text for s in spec.stages] + [s.copy_text for s in spec.slots])


def load(path: Path) -> list[Event]:
    events: list[Event] = []
    start: datetime | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        body = json.loads(line)
        at = datetime.fromisoformat(body["recv_at"])
        start = start or at
        adapter = PLAYER_MESSAGES if body["channel"] == "player" else CONSOLE_MESSAGES
        message = adapter.validate_python(body["message"])
        # The console receives creative copies too; the player's are what people saw.
        if isinstance(message, CreativeReady | CreativeUpgraded) and body["channel"] != "player":
            continue
        events.append(Event((at - start).total_seconds(), message))
    return events


def _first(events: Iterable[Event], after: float, test: Callable[[Any], bool]) -> Event | None:
    return next((e for e in events if e.t_s >= after and test(e.message)), None)


def _creative(test: Callable[[CreativeSpec], bool]) -> Callable[[Any], bool]:
    def check(message: Any) -> bool:
        return (
            isinstance(message, CreativeReady | CreativeUpgraded)
            and message.auction_id is not None
            and test(message.spec)
        )

    return check


BEATS: tuple[tuple[str, Callable[[Any], bool]], ...] = (
    (
        "2. Dúo $5.990 (two people, bottle + cups)",
        _creative(lambda s: _brands(s) == BASKET and "$5.990" in _copy(s)),
    ),
    (
        "2. Trío $8.490 (a third person)",
        _creative(lambda s: _brands(s) == BASKET and "$8.490" in _copy(s)),
    ),
    (
        "3. Complete the basket (Café Patio cup marker)",
        _creative(lambda s: "Café Patio" in _brands(s) and "le falta" in _copy(s)),
    ),
    (
        "4. Bidding war (Rayo takes Voltio's seat)",
        _creative(lambda s: _brands(s) == RIVAL_BASKET),
    ),
    (
        "5. QR redeemed, both brands credited",
        lambda m: isinstance(m, ConsoleRedemption) and len(m.brands) >= 2,
    ),
    (
        "5. Receipt bills the basket for attention",
        lambda m: (
            isinstance(m, ConsoleReceipt) and Decimal(m.total.amount) > 0 and len(m.charges) >= 2
        ),
    ),
    (
        '5. "Charged $0: nobody looked"',
        lambda m: isinstance(m, ConsoleReceipt) and Decimal(m.total.amount) == 0,
    ),
)


def score(events: list[Event]) -> list[BeatResult]:
    """Each beat must land after the one before it (the script's order)."""
    results: list[BeatResult] = []
    after = 0.0
    for name, test in BEATS:
        hit = _first(events, after, test)
        if hit is None:
            results.append(BeatResult(name, False, None, "not seen after the previous beat"))
            continue
        after = hit.t_s
        results.append(BeatResult(name, True, round(hit.t_s, 1), _evidence(hit.message)))
    return results


def _evidence(message: Any) -> str:
    if isinstance(message, CreativeReady | CreativeUpgraded):
        spec = message.spec
        return f"{' + '.join(sorted(_brands(spec)))} · {spec.stages[0].copy_text if spec.stages else ''}"
    if isinstance(message, ConsoleRedemption):
        return f"{message.code} → {', '.join(message.brands)}"
    if isinstance(message, ConsoleReceipt):
        return f"${message.total.amount} · {message.attention_seconds} AS"
    return type(message).__name__


def render(path: Path, results: list[BeatResult]) -> str:
    hits = sum(r.hit for r in results)
    lines = [
        f"# Demo beats: `{path.name}`",
        "",
        f"**{'PASS' if hits == len(results) else 'INCOMPLETE'}** · {hits}/{len(results)} beats",
        "",
        "| | Beat | t (s) | Evidence |",
        "|---|---|---:|---|",
    ]
    lines += [
        f"| {'✅' if r.hit else '❌'} | {r.beat} | {r.t_s if r.t_s is not None else '—'} | "
        f"{r.evidence} |"
        for r in results
    ]
    return "\n".join(lines) + "\n"
