"""The model behind the service: a small port, the real YOLO26 adapter, and a fake.

`InferenceModel.infer` takes a decoded HxWx3 **BGR** uint8 array (what the
edge agent's local detector passes to Ultralytics) and returns wire-ready
dicts. The YOLO result parsing mirrors `edge/agent/vision/adapters.py`
(`_persons` / `_objects`) so remote and local detections are identical.

Nothing here keeps a frame after `infer` returns.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

logger = logging.getLogger(__name__)

type Person = dict[str, Any]  # {"box": [x1, y1, x2, y2], "confidence": f, "keypoints": [[x, y, c]]}
type Object = dict[str, Any]  # {"label": str, "box": [x1, y1, x2, y2], "confidence": f}


@dataclass(frozen=True, slots=True)
class Inference:
    persons: list[Person] = field(default_factory=list)
    objects: list[Object] = field(default_factory=list)


class InferenceModel(Protocol):
    name: str

    def infer(self, image: np.ndarray, *, objects: bool = True) -> Inference: ...


# ---------------------------------------------------------------- YOLO26 (Ultralytics)


class YoloModel:
    """YOLO26 pose (people + keypoints) and YOLO26 detect (objects) on one device.

    With `tensorrt=True` each `.pt` is exported once to a TensorRT FP16
    `.engine` next to it (reused on later starts); any export failure falls
    back to the `.pt` weights so the service still starts.
    """

    def __init__(
        self,
        *,
        pose_model: str = "yolo26s-pose.pt",
        object_model: str | None = "yolo26s.pt",
        imgsz: int = 640,
        conf: float = 0.35,
        device: str | None = None,
        tensorrt: bool = False,
    ) -> None:
        from ultralytics import YOLO  # local import: optional dependency

        self._imgsz = imgsz
        self._conf = conf
        self._device = device
        pose_path = _maybe_engine(pose_model, "pose", tensorrt, imgsz, device)
        self._pose = YOLO(pose_path, task="pose")
        self._objects = None
        object_path = None
        if object_model:
            object_path = _maybe_engine(object_model, "detect", tensorrt, imgsz, device)
            self._objects = YOLO(object_path, task="detect")
        self._lock = threading.Lock()  # Ultralytics predictors aren't thread-safe
        self.name = f"yolo:{Path(pose_path).name}+{Path(object_path).name if object_path else '-'}"

    def warmup(self) -> None:
        self.infer(np.zeros((360, 640, 3), dtype=np.uint8))

    def infer(self, image: np.ndarray, *, objects: bool = True) -> Inference:
        with self._lock:
            pose = self._pose.predict(
                image,
                imgsz=self._imgsz,
                conf=self._conf,
                classes=[0],
                device=self._device,
                verbose=False,
            )[0]
            found: list[Object] = []
            if objects and self._objects is not None:
                result = self._objects.predict(
                    image, imgsz=self._imgsz, conf=self._conf, device=self._device, verbose=False
                )[0]
                found = parse_objects(result)
        return Inference(persons=parse_persons(pose), objects=found)


def _maybe_engine(weights: str, task: str, tensorrt: bool, imgsz: int, device: str | None) -> str:
    if not tensorrt or not weights.endswith(".pt"):
        return weights
    engine = Path(weights).with_suffix(".engine")
    if engine.exists():
        return str(engine)
    try:
        from ultralytics import YOLO

        logger.info("exporting %s to TensorRT FP16 (one-time, a few minutes)", weights)
        exported = YOLO(weights, task=task).export(
            format="engine", half=True, imgsz=imgsz, device=device or 0, verbose=False
        )
        return str(exported)
    except Exception:
        logger.exception("TensorRT export of %s failed; serving the .pt weights", weights)
        return weights


def parse_persons(result: Any) -> list[Person]:
    """Same reading as edge `adapters._persons`, as wire dicts."""
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return []
    xyxy = boxes.xyxy.cpu().numpy()
    scores = boxes.conf.cpu().numpy()
    kps = result.keypoints
    kp_xy = kps.xy.cpu().numpy() if kps is not None else None
    kp_conf = kps.conf.cpu().numpy() if kps is not None and kps.conf is not None else None
    people: list[Person] = []
    for i, (x1, y1, x2, y2) in enumerate(xyxy):
        points: list[list[float]] = []
        if kp_xy is not None:
            confs = kp_conf[i] if kp_conf is not None else [1.0] * len(kp_xy[i])
            points = [
                [float(x), float(y), float(c)] for (x, y), c in zip(kp_xy[i], confs, strict=False)
            ]
        people.append(
            {
                "box": [float(x1), float(y1), float(x2), float(y2)],
                "confidence": float(scores[i]),
                "keypoints": points,
            }
        )
    return people


def parse_objects(result: Any) -> list[Object]:
    """Same reading as edge `adapters._objects` (people are dropped), as wire dicts."""
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return []
    names = result.names
    out: list[Object] = []
    for (x1, y1, x2, y2), score, cls in zip(
        boxes.xyxy.cpu().numpy(),
        boxes.conf.cpu().numpy(),
        boxes.cls.cpu().numpy(),
        strict=False,
    ):
        label = str(names[int(cls)])
        if label == "person":
            continue
        out.append(
            {
                "label": label,
                "box": [float(x1), float(y1), float(x2), float(y2)],
                "confidence": float(score),
            }
        )
    return out


# ---------------------------------------------------------------- fake (tests, benches)


def _default_canned(width: int, height: int) -> Inference:
    cx, top = width / 2, height * 0.1
    keypoints = [[cx, top + 30, 0.9], [cx + 10, top + 20, 0.9], [cx - 10, top + 20, 0.9]]
    keypoints += [[cx, top + 40, 0.5]] * 14
    return Inference(
        persons=[
            {
                "box": [cx - width * 0.1, top, cx + width * 0.1, height * 0.95],
                "confidence": 0.9,
                "keypoints": keypoints,
            }
        ],
        objects=[
            {
                "label": "cup",
                "box": [cx + 20, height * 0.5, cx + 60, height * 0.6],
                "confidence": 0.8,
            }
        ],
    )


class FakeModel:
    """No download, no GPU. Returns canned detections after an optional delay.

    `canned` fixes the output (coordinates in the received JPEG's pixels);
    otherwise one person and one cup are placed relative to the image size.
    `seen_shapes` records image *shapes* only (never pixels) for tests.
    """

    def __init__(self, canned: Inference | None = None, *, delay_ms: float = 0.0) -> None:
        self._canned = canned
        self._delay_s = delay_ms / 1000
        self.seen_shapes: list[tuple[int, ...]] = []
        self.name = "fake"

    @classmethod
    def from_file(cls, path: str | None, *, delay_ms: float = 0.0) -> FakeModel:
        if not path:
            return cls(delay_ms=delay_ms)
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            Inference(persons=raw.get("persons", []), objects=raw.get("objects", [])),
            delay_ms=delay_ms,
        )

    def warmup(self) -> None:
        return None

    def infer(self, image: np.ndarray, *, objects: bool = True) -> Inference:
        self.seen_shapes.append(tuple(image.shape))
        if self._delay_s:
            time.sleep(self._delay_s)
        height, width = image.shape[:2]
        canned = self._canned or _default_canned(width, height)
        return Inference(persons=canned.persons, objects=canned.objects if objects else [])
