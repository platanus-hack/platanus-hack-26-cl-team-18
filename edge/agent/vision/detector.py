"""The `Detector` port and the failover between remote GPU and local inference (ADR 0007).

`FailoverDetector` is pure orchestration (no network, no models), so its
switching rules are unit-tested with fakes:

- primary (remote GPU) is used while healthy;
- it fails over to the secondary (local) when the primary raises, or when its
  p95 latency over the recent window exceeds the budget;
- it probes the primary again after `recovery` and switches back when a probe
  succeeds within budget.

Every switch is visible: `active` is reported in the snapshot's detector field
and the console shows it.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol

from agent.vision.types import FrameDetections

logger = logging.getLogger(__name__)

type Frame = Any  # an HxWx3 BGR numpy array; kept opaque so tests need no OpenCV


class Detector(Protocol):
    name: str

    def detect(self, frame: Frame, measured_at: datetime) -> FrameDetections: ...


class DetectorUnavailable(Exception):
    """Raised by an adapter that can't produce detections right now (socket down, timeout)."""


@dataclass(frozen=True, slots=True)
class FailoverConfig:
    budget_ms: float = 150.0  # p95 round trip allowed for the primary (ADR 0007)
    window: int = 20  # recent primary calls considered for p95
    min_samples: int = 5
    recovery_s: float = 10.0  # wait this long on the secondary before probing the primary

    def __post_init__(self) -> None:
        if not math.isfinite(self.budget_ms) or self.budget_ms <= 0:
            raise ValueError("budget_ms must be positive and finite")
        if not 1 <= self.min_samples <= self.window:
            raise ValueError("require 1 <= min_samples <= window")
        if not math.isfinite(self.recovery_s) or self.recovery_s < 0:
            raise ValueError("recovery_s must be non-negative and finite")


class FailoverDetector:
    def __init__(
        self,
        primary: Detector,
        secondary: Detector,
        config: FailoverConfig | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        primary_ready: bool = True,
    ) -> None:
        self._primary = primary
        self._secondary = secondary
        self._config = config or FailoverConfig()
        self._now = monotonic
        self._latencies: deque[float] = deque(maxlen=self._config.window)
        self._on_primary = primary_ready
        self._failed_over_at = 0.0 if primary_ready else self._now()
        self.name = "failover"

    @property
    def active(self) -> str:
        return self._primary.name if self._on_primary else self._secondary.name

    def detect(self, frame: Frame, measured_at: datetime) -> FrameDetections:
        started = self._now()
        attempted_primary = False
        if not self._on_primary and self._now() - self._failed_over_at >= self._config.recovery_s:
            attempted_primary = True
            probe = self._try_primary(frame, measured_at)
            if probe is not None:
                return probe
        if self._on_primary:
            attempted_primary = True
            result = self._try_primary(frame, measured_at)
            if result is not None:
                return result
        primary_ms = (self._now() - started) * 1000
        result = self._secondary.detect(frame, measured_at)
        if attempted_primary:
            return replace(
                result,
                inference_ms=max(result.inference_ms + primary_ms, (self._now() - started) * 1000),
                extras={**result.extras, "primary_attempt_ms": primary_ms},
            )
        return result

    def _try_primary(self, frame: Frame, measured_at: datetime) -> FrameDetections | None:
        started = self._now()
        try:
            result = self._primary.detect(frame, measured_at)
        except DetectorUnavailable as exc:
            self._fail_over(f"primary unavailable: {exc}")
            return None
        elapsed_ms = (self._now() - started) * 1000
        if not self._on_primary:
            # A probe: only switch back if it came in under budget.
            if elapsed_ms > self._config.budget_ms:
                self._failed_over_at = self._now()
                return None
            logger.info("primary detector recovered (%.0f ms); switching back", elapsed_ms)
            self._on_primary = True
            self._latencies.clear()
        self._latencies.append(elapsed_ms)
        if self._p95_over_budget():
            self._fail_over(f"primary p95 over {self._config.budget_ms:.0f} ms")
        return result

    def _p95_over_budget(self) -> bool:
        if len(self._latencies) < self._config.min_samples:
            return False
        ordered = sorted(self._latencies)
        p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
        return p95 > self._config.budget_ms

    def _fail_over(self, reason: str) -> None:
        if self._on_primary:
            logger.warning("failing over to %s: %s", self._secondary.name, reason)
        self._on_primary = False
        self._failed_over_at = self._now()
        self._latencies.clear()


class CompositeDetector:
    """Runs a person/object detector and a marker detector on the same frame."""

    def __init__(self, main: Detector, markers: Detector | None) -> None:
        self._main = main
        self._markers = markers
        self.name = main.name

    @property
    def active(self) -> str:
        return getattr(self._main, "active", self._main.name)

    def detect(self, frame: Frame, measured_at: datetime) -> FrameDetections:
        result = self._main.detect(frame, measured_at)
        if self._markers is None:
            return result
        found = self._markers.detect(frame, measured_at)
        return FrameDetections(
            measured_at=result.measured_at,
            width=result.width,
            height=result.height,
            persons=result.persons,
            objects=result.objects,
            markers=found.markers,
            detector=result.detector,
            inference_ms=result.inference_ms + found.inference_ms,
            extras=result.extras,
        )
