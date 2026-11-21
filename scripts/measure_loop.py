"""Measure the full loop on a running backend: perception → auction → creative → display → bill → redeem.

    uv run python -m scripts.measure_loop --server http://127.0.0.1:8000 --cycles 12

Acts as the agent (or rides along with a real one: --observe-only), as a player
(?attention=1, reports player.displayed on arrival and playback.report on
replacement, like web/src/features/screen), as the console, and as a judge's
phone redeeming the QR. Prints a JSON report and a Markdown table. Server-side
instants come from each creative's LatencyTrace; the "display" instant is this
client's receive time, so it includes the network but not browser decode/paint.
"""

from __future__ import annotations

import argparse
import json
import secrets
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime

from scripts.replay_audience import paid_brands, snapshot_for
from websockets.sync.client import connect

from app.contract.messages import (
    CONSOLE_OUTBOUND,
    PLAYER_OUTBOUND,
    ConsoleReceipt,
    ConsoleRedemption,
    CreativeReady,
    PlaybackReport,
    PlayerDisplayed,
    ScreenAttention,
)

SCENES = ("fitness", "co-brand", "adult", "minor-safe", "co-brand", "empty")


def ms(a: datetime | None, b: datetime | None) -> float | None:
    return None if a is None or b is None else (b - a).total_seconds() * 1000


def pct(values: list[float], p: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, round(p / 100 * len(ordered)) - 1))], 1)


@dataclass
class Report:
    cycles: int = 0
    creatives: int = 0
    paid_creatives: int = 0
    with_qr: int = 0
    with_video: int = 0
    attention_messages: int = 0
    server_decision_ms: list[float] = field(default_factory=list)  # received → ready
    agent_to_display_ms: list[float] = field(default_factory=list)  # measured → shown, minus hold
    hold_ms: list[float] = field(default_factory=list)
    receipts: int = 0
    attention_seconds: float = 0.0
    charged_clp: int = 0
    zero_charges: int = 0
    redemptions: int = 0
    redemption_ms: list[float] = field(default_factory=list)
    wrong_creatives: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        return {
            "cycles": self.cycles,
            "creatives": self.creatives,
            "paid_creatives": self.paid_creatives,
            "paid_with_qr": self.with_qr,
            "paid_with_video_loop": self.with_video,
            "attention_messages": self.attention_messages,
            "server_decision_ms_p50": pct(self.server_decision_ms, 50),
            "server_decision_ms_p95": pct(self.server_decision_ms, 95),
            "agent_to_display_ms_p50": pct(self.agent_to_display_ms, 50),
            "agent_to_display_ms_p95": pct(self.agent_to_display_ms, 95),
            "intentional_hold_ms_p50": pct(self.hold_ms, 50),
            "receipts": self.receipts,
            "attention_seconds_billed": round(self.attention_seconds, 3),
            "charged_clp": self.charged_clp,
            "zero_attention_charges": self.zero_charges,
            "redemptions": self.redemptions,
            "redeem_round_trip_ms_p50": pct(self.redemption_ms, 50),
            "wrong_creatives": self.wrong_creatives,
        }


EXPECTED = {
    "fitness": {"Voltio"},
    "co-brand": {"Voltio", "Café Patio"},
    "adult": {"Malta Sur"},
    "minor-safe": set(),
    "empty": set(),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--screen", default="demo-01")
    parser.add_argument("--cycles", type=int, default=6)
    parser.add_argument("--dwell", type=float, default=4.0, help="seconds per scene")
    args = parser.parse_args()
    ws = args.server.replace("http", "ws", 1)
    report = Report()
    lock = threading.Lock()
    stop = threading.Event()

    def console_reader() -> None:
        with connect(f"{ws}/ws/console", open_timeout=5) as console:
            while not stop.is_set():
                try:
                    message = CONSOLE_OUTBOUND.validate_json(console.recv(timeout=0.5))
                except TimeoutError:
                    continue
                with lock:
                    if isinstance(message, ConsoleReceipt):
                        report.receipts += 1
                        report.attention_seconds += float(message.attention_seconds)
                        report.charged_clp += int(message.total.amount)
                        report.zero_charges += int(float(message.attention_seconds) == 0)
                    elif isinstance(message, ConsoleRedemption):
                        report.redemptions += 1

    reader = threading.Thread(target=console_reader, daemon=True)
    reader.start()
    time.sleep(0.3)
    with (
        connect(f"{ws}/ws/screen/{args.screen}?attention=1", open_timeout=5) as player,
        connect(f"{ws}/ws/agent", open_timeout=5) as agent,
    ):
        seq = display_seq = play_seq = 0
        on_screen: CreativeReady | None = None
        shown_at: datetime | None = None

        def show(creative: CreativeReady) -> None:
            nonlocal on_screen, shown_at, display_seq, play_seq
            now = datetime.now(UTC)
            if on_screen is not None and on_screen.creative_id != creative.creative_id:
                play_seq += 1
                assert shown_at is not None
                player.send(
                    PlaybackReport(
                        screen_id=args.screen,
                        creative_id=on_screen.creative_id,
                        seq=play_seq,
                        started_at=shown_at,
                        ended_at=now,
                    ).model_dump_json()
                )
            display_seq += 1
            player.send(
                PlayerDisplayed(
                    screen_id=args.screen,
                    creative_id=creative.creative_id,
                    seq=display_seq,
                    displayed_at=now,
                ).model_dump_json()
            )
            on_screen, shown_at = creative, now
            report.creatives += 1
            trace = creative.trace
            if (decision := ms(trace.received_at, trace.ready_at)) is not None:
                hold = max(0.0, ms(trace.cleared_at, trace.swap_not_before) or 0.0)
                report.server_decision_ms.append(max(0.0, decision - hold))
                report.hold_ms.append(hold)
                if (g2g := ms(trace.measured_at, now)) is not None:
                    report.agent_to_display_ms.append(max(0.0, g2g - hold))
            if creative.auction_id is not None:
                report.paid_creatives += 1
                report.with_qr += int(creative.spec.qr_url is not None)
                report.with_video += int(creative.spec.video_url is not None)
                if creative.spec.redemption_code:
                    started = time.monotonic()
                    page = urllib.request.urlopen(
                        f"{args.server}/r/{creative.spec.redemption_code}"
                    ).read()
                    assert b"Canjear" in page
                    request = urllib.request.Request(
                        f"{args.server}/r/{creative.spec.redemption_code}/redeem",
                        json.dumps({"nonce": secrets.token_hex(8)}).encode(),
                        {"content-type": "application/json"},
                    )
                    json.loads(urllib.request.urlopen(request).read())
                    report.redemption_ms.append((time.monotonic() - started) * 1000)

        def pump(until: float) -> None:
            while time.monotonic() < until:
                try:
                    message = PLAYER_OUTBOUND.validate_json(player.recv(timeout=0.1))
                except TimeoutError:
                    continue
                if isinstance(message, ScreenAttention):
                    report.attention_messages += 1
                elif isinstance(message, CreativeReady):
                    show(message)

        pump(time.monotonic() + 0.5)  # the house ad on connect
        for cycle in range(args.cycles):
            scene = SCENES[cycle % len(SCENES)]
            deadline = time.monotonic() + args.dwell
            while time.monotonic() < deadline:  # a 1 Hz heartbeat, like the real agent
                seq += 1
                agent.send(snapshot_for(scene, seq, datetime.now(UTC)).model_dump_json())
                pump(min(deadline, time.monotonic() + 1.0))
            report.cycles += 1
            if on_screen is not None and set(paid_brands(on_screen)) != EXPECTED[scene]:
                report.wrong_creatives.append(f"{scene}: {sorted(paid_brands(on_screen))}")
        agent.send(snapshot_for("empty", seq + 1, datetime.now(UTC)).model_dump_json())
        pump(time.monotonic() + 4)
    time.sleep(1.0)
    stop.set()
    reader.join(2)
    summary = report.summary()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\n| metric | value |\n|---|---|")
    for key, value in summary.items():
        print(f"| {key} | {value} |")


if __name__ == "__main__":
    main()
