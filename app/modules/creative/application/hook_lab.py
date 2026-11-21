"""Hook testing on air: which approved hook to show, and what each one earned.

In memory and bounded (a demo screen, not a warehouse). Keyed by the brief's
signature, so only briefs that deserve the same copy share a test. Thread-safe:
composition runs on worker threads, playback and redemption callbacks elsewhere.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from threading import Lock

from app.modules.creative.domain.hook_test import ArmStats, pick_arm

MAX_TESTS = 512
MAX_AIRED = 2048


@dataclass(frozen=True, slots=True, kw_only=True)
class HookResult:
    key: str
    variant: int
    hook: str
    stats: ArmStats


class HookLab:
    def __init__(self) -> None:
        self._arms: OrderedDict[str, list[ArmStats]] = OrderedDict()
        self._hooks: dict[tuple[str, int], str] = {}
        self._aired: OrderedDict[str, tuple[str, int]] = OrderedDict()
        self._lock = Lock()

    def variant_for(self, key: str, options: int) -> int:
        """The option to air for this brief signature (0 when there is nothing to test)."""
        if options <= 1:
            return 0
        with self._lock:
            arms = self._arms.setdefault(key, [])
            arms.extend(ArmStats() for _ in range(options - len(arms)))
            self._arms.move_to_end(key)
            while len(self._arms) > MAX_TESTS:
                self._arms.popitem(last=False)
            return pick_arm(arms[:options])

    def aired(self, creative_id: str, key: str, variant: int, hook: str) -> None:
        with self._lock:
            self._aired[creative_id] = (key, variant)
            self._hooks[(key, variant)] = hook
            while len(self._aired) > MAX_AIRED:
                self._aired.popitem(last=False)

    def observe_play(self, creative_id: str, attention_s: Decimal) -> None:
        self._update(creative_id, lambda arm: arm.played(attention_s))

    def observe_scan(self, creative_id: str) -> None:
        self._update(creative_id, lambda arm: arm.scanned())

    def results(self) -> list[HookResult]:
        """Every tested option with its stats, best mean first within each brief."""
        with self._lock:
            rows = [
                HookResult(key=key, variant=i, hook=self._hooks.get((key, i), ""), stats=arm)
                for key, arms in self._arms.items()
                for i, arm in enumerate(arms)
            ]
        return sorted(rows, key=lambda r: (r.key, -r.stats.mean_reward(), r.variant))

    def _update(self, creative_id: str, change: Callable[[ArmStats], ArmStats]) -> None:
        with self._lock:
            aired = self._aired.get(creative_id)
            if aired is None:
                return  # house ad, an untested brief, or forgotten
            key, variant = aired
            arms = self._arms.get(key)
            if arms is None or variant >= len(arms):
                return
            arms[variant] = change(arms[variant])
