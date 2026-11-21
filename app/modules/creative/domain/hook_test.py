"""Which approved hook to air for a brief: a deterministic multi-armed bandit (pure).

Every option is already guard-clean and approved, so testing them never risks what
airs; it only learns which line holds attention. Reward per play is the measured
attention-seconds plus a QR scan valued as SCAN_WORTH_S seconds (a scan is the sale
the brands pay for). UCB1 picks: untried options first (lowest index), then the best
upper bound; ties go to the lowest index, so the choice is reproducible from the stats.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

SCAN_WORTH_S = Decimal(10)
# Scale of the exploration bonus, in attention-seconds (plays last ~3-12 s).
EXPLORATION_S = Decimal(4)


@dataclass(frozen=True, slots=True)
class ArmStats:
    plays: int = 0
    attention_s: Decimal = Decimal(0)
    scans: int = 0

    def __post_init__(self) -> None:
        if self.plays < 0 or self.scans < 0 or self.attention_s < 0:
            raise ValueError("hook stats cannot be negative")

    def played(self, attention_s: Decimal) -> ArmStats:
        if attention_s < 0:
            raise ValueError("attention cannot be negative")
        return replace(self, plays=self.plays + 1, attention_s=self.attention_s + attention_s)

    def scanned(self) -> ArmStats:
        return replace(self, scans=self.scans + 1)

    def mean_reward(self) -> Decimal:
        """Attention-seconds per play, a scan counted as SCAN_WORTH_S seconds."""
        if self.plays == 0:
            return Decimal(0)
        return (self.attention_s + SCAN_WORTH_S * self.scans) / self.plays


def pick_arm(arms: Sequence[ArmStats], *, exploration: Decimal = EXPLORATION_S) -> int:
    """The option to air next: untried first, then UCB1 (mean + c·√(ln N / n))."""
    if not arms:
        raise ValueError("pick_arm needs at least one option")
    for index, arm in enumerate(arms):
        if arm.plays == 0:
            return index
    total = Decimal(sum(arm.plays for arm in arms))
    log_total = total.ln()

    def bound(arm: ArmStats) -> Decimal:
        return arm.mean_reward() + exploration * (log_total / arm.plays).sqrt()

    best = max(range(len(arms)), key=lambda i: (bound(arms[i]), -i))
    return best
