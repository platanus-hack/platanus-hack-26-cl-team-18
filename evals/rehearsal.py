"""Record a live rehearsal (or the real demo) and score it afterwards.

The recorder joins as one more console and one more player of the demo
screen. The hub fans out to every sink, so it never disturbs the real screen.
It stores wire messages only (decisions, prices, counts, segment ids, latency
traces), never frames, audio or transcripts, so a recording is safe to keep.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from app.bootstrap.demo import DEMO_SCREEN
from app.contract.messages import ConsoleAuction, ConsoleMoment, CreativeReady
from evals.live import CONSOLE_MESSAGES, PLAYER_MESSAGES
from evals.metrics import Decision


def record(
    server: str, out: Path, *, duration_s: float | None = None, screen_id: str = DEMO_SCREEN
) -> int:
    """Append every player/console message to ``out`` as JSONL until Ctrl-C or the duration."""
    server = server.rstrip("/")
    count = 0
    deadline = None if duration_s is None else time.monotonic() + duration_s
    out.parent.mkdir(parents=True, exist_ok=True)
    with (
        connect(f"{server}/ws/screen/{screen_id}") as player,
        connect(f"{server}/ws/console") as console,
        out.open("a", encoding="utf-8") as sink,
    ):
        try:
            while deadline is None or time.monotonic() < deadline:
                for channel, conn in (("player", player), ("console", console)):
                    try:
                        raw = conn.recv(timeout=0.05)
                    except TimeoutError:
                        continue
                    recv_at = datetime.now(UTC).isoformat()
                    sink.write(
                        json.dumps(
                            {"recv_at": recv_at, "channel": channel, "message": json.loads(raw)}
                        )
                        + "\n"
                    )
                    sink.flush()
                    count += 1
        except KeyboardInterrupt, ConnectionClosed:
            pass
    return count


def decisions_from_recording(path: Path) -> tuple[list[Decision], float]:
    """Pair each player creative with its console explanation; returns (decisions, duration_s)."""
    creatives: list[CreativeReady] = []
    auctions: dict[str, ConsoleAuction] = {}
    minor_by_moment: dict[str, bool] = {}
    auction_by_creative_trace: dict[str, ConsoleAuction] = {}
    stamps: list[datetime] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        body = json.loads(line)
        stamps.append(datetime.fromisoformat(body["recv_at"]))
        adapter = PLAYER_MESSAGES if body["channel"] == "player" else CONSOLE_MESSAGES
        message = adapter.validate_python(body["message"])
        if isinstance(message, CreativeReady) and body["channel"] == "player":
            creatives.append(message)
        elif isinstance(message, ConsoleAuction):
            auctions[message.auction_id] = message
            if message.trace.ready_at is not None:
                auction_by_creative_trace[message.trace.ready_at.isoformat()] = message
        elif isinstance(message, ConsoleMoment):
            minor_by_moment[message.moment_id] = message.minor_likely.value
    decisions = []
    for index, creative in enumerate(creatives):
        auction = auctions.get(creative.auction_id or "")
        if auction is None and creative.trace.ready_at is not None:
            auction = auction_by_creative_trace.get(creative.trace.ready_at.isoformat())
        decisions.append(
            Decision(
                source=f"rehearsal:{path.name}",
                step=index,
                minor_likely=minor_by_moment.get(auction.moment_id, False) if auction else False,
                creative=creative,
                auction=auction,
            )
        )
    duration = (stamps[-1] - stamps[0]).total_seconds() if len(stamps) > 1 else 0.0
    return decisions, duration
