"""Local ByteTrack association of YOLO boxes, independent of inference (ADR 0007).

Only boxes and confidence enter ByteTrack. No frames, appearance features,
embeddings or model calls are needed. Provider keys remain inside the agent;
PersonTracker owns the session-counter ids, dwell and attention facts.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from agent.vision.tracking import TrackerConfig
from agent.vision.types import PersonDetection


class ByteTrackAssociator:
    def __init__(self, config: TrackerConfig | None = None) -> None:
        from ultralytics.engine.results import Boxes
        from ultralytics.trackers.byte_tracker import BYTETracker

        config = config or TrackerConfig()
        self._boxes = Boxes
        self._tracker = BYTETracker(
            SimpleNamespace(
                track_high_thresh=config.min_person_score,
                track_low_thresh=config.min_recovery_score,
                new_track_thresh=config.min_person_score,
                track_buffer=30,
                match_thresh=1 - config.min_iou,
                fuse_score=False,
            ),
        )

    def update(
        self, people: list[PersonDetection], width: int, height: int
    ) -> list[tuple[int, PersonDetection]]:
        rows = np.asarray(
            [[p.box.x1, p.box.y1, p.box.x2, p.box.y2, p.score, 0] for p in people],
            dtype=np.float32,
        ).reshape(-1, 6)
        tracked = self._tracker.update(self._boxes(rows, orig_shape=(height, width)))
        # Use each matched observation's pose and box, not Kalman-predicted
        # geometry: head redaction, looking and minor safety need current facts.
        return [(int(row[4]), people[int(row[-1])]) for row in tracked]

    def reset(self) -> None:
        self._tracker.reset()
