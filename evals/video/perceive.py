# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "ultralytics>=8.3.200",
#     "torch>=2.4",
#     "torchvision>=0.19",
# ]
# [tool.uv.sources]
# torch = { index = "pytorch-cpu" }
# torchvision = { index = "pytorch-cpu" }
# [[tool.uv.index]]
# name = "pytorch-cpu"
# url = "https://download.pytorch.org/whl/cpu"
# explicit = true
# ///
"""Real perception on a real video file: YOLO pose + objects -> agent snapshots.

Eval stand-in for the vision agent's replay mode (docs/evals.md §4) until
edge/agent/vision lands. It writes the same JSONL the agent will write,
``{"t_s": .., "snapshot": AudienceSnapshot, "raw": {...}}``, so the scorer
and the live-backend replay don't care which one produced it.

Runs on CPU in its own environment (``uv run --script``), so torch never
enters the backend venv or CI. Privacy: frames stay in memory; the only
images written are downscaled keyframes with every detected head blurred,
saved under the gitignored results directory for human review.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

NEAR_SCREEN_MIN_HEIGHT = 0.18  # person box height / frame height: close enough to read a screen
KEYPOINT_CONF = 0.5
NOSE, LEFT_EYE, RIGHT_EYE, LEFT_EAR, RIGHT_EAR = range(5)
IGNORED_OBJECTS = {"person", "car", "truck", "bus", "motorcycle", "bicycle", "train", "boat"}


def load_model(candidates: list[str]) -> tuple[YOLO, str]:
    errors = []
    for name in candidates:
        try:
            return YOLO(name), name
        except Exception as exc:  # unknown weights in this ultralytics version
            errors.append(f"{name}: {exc}")
    raise SystemExit("no model could load:\n" + "\n".join(errors))


def looking_at_camera(kpts: np.ndarray, conf: np.ndarray) -> bool:
    """Frontal face from pose keypoints: nose and both eyes visible, nose between the eyes."""
    if min(conf[NOSE], conf[LEFT_EYE], conf[RIGHT_EYE]) < KEYPOINT_CONF:
        return False
    left, right = sorted((kpts[LEFT_EYE][0], kpts[RIGHT_EYE][0]))
    return left <= kpts[NOSE][0] <= right


def head_box(box: np.ndarray, kpts: np.ndarray, conf: np.ndarray) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    head = [kpts[i] for i in (NOSE, LEFT_EYE, RIGHT_EYE, LEFT_EAR, RIGHT_EAR) if conf[i] > 0.3]
    if len(head) >= 2:
        xs, ys = [p[0] for p in head], [p[1] for p in head]
        size = max(max(xs) - min(xs), (y2 - y1) * 0.12) * 1.8
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        return int(cx - size / 2), int(cy - size * 0.6), int(cx + size / 2), int(cy + size * 0.5)
    return int(x1), int(y1), int(x2), int(y1 + (y2 - y1) * 0.22)


def blur_heads(frame: np.ndarray, heads: list[tuple[int, int, int, int]]) -> np.ndarray:
    out = frame.copy()
    height, width = out.shape[:2]
    for x1, y1, x2, y2 in heads:
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
        if x2 - x1 > 2 and y2 - y1 > 2:
            region = out[y1:y2, x1:x2]
            out[y1:y2, x1:x2] = cv2.GaussianBlur(region, (0, 0), sigmaX=max(6, (x2 - x1) / 4))
    return out


def overlaps(a: np.ndarray, b: np.ndarray, pad: float) -> bool:
    bx1, by1, bx2, by2 = b
    px, py = (bx2 - bx1) * pad, (by2 - by1) * pad
    return not (a[2] < bx1 - px or a[0] > bx2 + px or a[3] < by1 - py or a[1] > by2 + py)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="recording JSONL")
    parser.add_argument("--keyframes", type=Path, help="directory for blurred keyframes")
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--debounce-ms", type=float, default=500.0)
    parser.add_argument("--conf", type=float, default=0.35)
    parser.add_argument("--pose-model", default="yolo26n-pose.pt,yolo11n-pose.pt")
    parser.add_argument("--det-model", default="yolo26s.pt,yolo11s.pt")
    args = parser.parse_args()

    pose_model, pose_name = load_model(args.pose_model.split(","))
    det_model, det_name = load_model(args.det_model.split(","))
    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        raise SystemExit(f"cannot open {args.video}")
    source_fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    capture.set(cv2.CAP_PROP_POS_MSEC, args.start * 1000)
    step = max(1, round(source_fps / args.fps))
    if args.keyframes:
        args.keyframes.mkdir(parents=True, exist_ok=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    clock0 = datetime(2026, 11, 21, tzinfo=UTC)
    published: tuple | None = None
    candidate: tuple | None = None
    candidate_since = 0.0
    seq = 0
    frame_index = 0
    infer_ms: list[float] = []
    raw_changes = published_changes = 0
    previous_raw: tuple | None = None
    with args.out.open("w", encoding="utf-8") as sink:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            t = frame_index / source_fps
            frame_index += 1
            if t > args.duration:
                break
            if (frame_index - 1) % step:
                continue
            height = frame.shape[0]
            started = time.perf_counter()
            pose = pose_model(frame, classes=[0], conf=args.conf, verbose=False)[0]
            det = det_model(frame, conf=args.conf, verbose=False)[0]
            infer_ms.append((time.perf_counter() - started) * 1000)

            boxes = pose.boxes.xyxy.cpu().numpy() if pose.boxes is not None else np.empty((0, 4))
            kpts = pose.keypoints.xy.cpu().numpy() if pose.keypoints is not None else None
            kconf = (
                pose.keypoints.conf.cpu().numpy()
                if pose.keypoints is not None and pose.keypoints.conf is not None
                else None
            )
            near, looking, heads = [], 0, []
            for i, box in enumerate(boxes):
                k = kpts[i] if kpts is not None else np.zeros((17, 2))
                c = kconf[i] if kconf is not None else np.zeros(17)
                heads.append(head_box(box, k, c))
                if (box[3] - box[1]) / height >= NEAR_SCREEN_MIN_HEIGHT:
                    near.append(box)
                    looking += int(looking_at_camera(k, c))
            objects: dict[str, float] = {}
            for box, cls, score in zip(
                det.boxes.xyxy.cpu().numpy(),
                det.boxes.cls.cpu().numpy(),
                det.boxes.conf.cpu().numpy(),
                strict=True,
            ):
                label = det.names[int(cls)]
                if label in IGNORED_OBJECTS or not any(overlaps(box, p, 0.15) for p in near):
                    continue  # held/near products only; objects only, never a person attribute
                objects[label] = max(objects.get(label, 0.0), round(float(score), 2))

            raw = (len(near), looking, tuple(sorted(objects)))
            raw_changes += int(previous_raw is not None and raw != previous_raw)
            previous_raw = raw
            # Agent-side debounce: a new fact set must hold for debounce_ms first.
            if raw != candidate:
                candidate, candidate_since = raw, t
            if published is None or (
                candidate != published and (t - candidate_since) * 1000 >= args.debounce_ms
            ):
                published_changes += int(published is not None)
                published = candidate
                published_objects = dict(objects)
                if args.keyframes:
                    small = blur_heads(frame, heads)
                    scale = 480 / small.shape[1]
                    small = cv2.resize(small, None, fx=scale, fy=scale)
                    cv2.imwrite(str(args.keyframes / f"{t:07.2f}.jpg"), small, [cv2.IMWRITE_JPEG_QUALITY, 80])
            people, look, _ = published
            seq += 1
            snapshot = {
                "v": 1,
                "type": "audience.snapshot",
                "screen_id": "replay",
                "seq": seq,
                "measured_at": (clock0 + timedelta(seconds=t)).isoformat(),
                "people": people,
                "looking": look,
                "objects": [{"label": k, "score": v} for k, v in sorted(published_objects.items())],
                "sound_scene": [],
                # Not inferred yet: no age model in this stand-in. Safety is
                # scored on labelled clips once the real agent emits it.
                "minor_likely": {"value": False, "confidence": 0.0},
            }
            raw_facts = {"people": raw[0], "looking": raw[1], "objects": objects}
            sink.write(json.dumps({"t_s": round(t, 3), "snapshot": snapshot, "raw": raw_facts}) + "\n")

    meta = {
        "video": args.video.name,
        "pose_model": pose_name,
        "det_model": det_name,
        "samples": seq,
        "sample_fps": args.fps,
        "debounce_ms": args.debounce_ms,
        "cpu_infer_p50_ms": statistics.median(infer_ms) if infer_ms else None,
        "cpu_infer_max_ms": max(infer_ms, default=None),
        "raw_fact_changes": raw_changes,
        "published_changes": published_changes,
    }
    args.out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
