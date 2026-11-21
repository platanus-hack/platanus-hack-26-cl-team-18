"""Replay a synthetic audience on the existing agent WebSocket."""

import argparse
from datetime import UTC, datetime

from websockets.sync.client import ClientConnection, connect

from app.bootstrap.demo import DEMO_SCREEN
from app.contract.messages import (
    AudienceSnapshot,
    CreativeReady,
    DetectedObject,
    MinorLikely,
    SoundScene,
)

SCENES: dict[str, tuple[str, ...]] = {
    "co-brand": ("Voltio", "Café Patio"),
    "fitness": ("Voltio",),
    "adult": ("Malta Sur",),
    "minor-safe": (),
    "empty": (),
}


def snapshot_for(scene: str, seq: int, measured_at: datetime) -> AudienceSnapshot:
    if scene in {"co-brand", "fitness"}:
        objects = [DetectedObject(label="sports ball", score=1)]
    elif scene in {"adult", "minor-safe"}:
        objects = [DetectedObject(label="tap handle fixture", score=1)]
    else:
        objects = []
    count = 2 if scene == "co-brand" else int(scene != "empty")
    return AudienceSnapshot(
        screen_id=DEMO_SCREEN,
        seq=seq,
        measured_at=measured_at,
        people=count,
        looking=count,
        objects=objects,
        sound_scene=(
            [SoundScene(label="coffee service fixture", score=1)] if scene == "co-brand" else []
        ),
        minor_likely=MinorLikely(value=scene == "minor-safe", confidence=1),
    )


def paid_brands(creative: CreativeReady) -> tuple[str, ...]:
    if creative.auction_id is None:
        return ()
    return tuple(slot.brand_name for slot in creative.spec.slots)


def wait_for_scene(player: ClientConnection, scene: str, *, after: str) -> CreativeReady:
    while True:
        creative = CreativeReady.model_validate_json(player.recv(timeout=5))
        if creative.creative_id == after:
            continue
        if set(paid_brands(creative)) != set(SCENES[scene]):
            raise RuntimeError(f"Unexpected creative for scene {scene!r}")
        return creative


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="ws://127.0.0.1:8000")
    parser.add_argument("--scene", choices=SCENES, default="co-brand")
    args = parser.parse_args()
    server = args.server.rstrip("/")

    try:
        with (
            connect(f"{server}/ws/screen/{DEMO_SCREEN}") as player,
            connect(f"{server}/ws/agent") as agent,
        ):
            current = CreativeReady.model_validate_json(player.recv(timeout=5))
            seq = 1
            if set(paid_brands(current)) == set(SCENES[args.scene]):
                reset = "empty" if SCENES[args.scene] else "fitness"
                agent.send(snapshot_for(reset, seq, datetime.now(UTC)).model_dump_json())
                current = wait_for_scene(player, reset, after=current.creative_id)
                seq += 1
            agent.send(snapshot_for(args.scene, seq, datetime.now(UTC)).model_dump_json())
            creative = wait_for_scene(player, args.scene, after=current.creative_id)
    except (OSError, RuntimeError) as exc:
        parser.exit(1, f"Replay failed. Start the backend with DEMO_SIMULATOR=true. {exc}\n")

    brands = " + ".join(paid_brands(creative)) or "house ad"
    print(f"{DEMO_SCREEN} · {creative.spec.layout_id} · {brands}")


if __name__ == "__main__":
    main()
