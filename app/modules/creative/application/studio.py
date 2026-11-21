"""The creative studio: hot composition plus warm, guarded, budgeted AI upgrades.

- `compose(brief)` (hot, sub-millisecond): cached AI copy for this brief's
  signature, else targeted stock copy. Never awaits anything.
- `upgrade_copy(brief, ...)` (warm): ROI + hourly budget check → `CopyWriter`
  with a timeout → guard → score against stock → cache. Returns the draft only
  if it is safe *and* at least as good as what is already on air.
- `prewarm(briefs)` (cold): the same path for briefs expected in the demo.

Every attempt leaves a `GenerationRecord` (latency, cost, score, verdict,
model version) for the console and the eval.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from app.modules.common.application.clock import BaseClock
from app.modules.creative.application.copy_cache import CopyCache
from app.modules.creative.application.ports.copywriter import (
    CopyFailure,
    CopyWriter,
    CopyWriterError,
    TokenUsage,
)
from app.modules.creative.application.stock_copy import StockCopywriter
from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.cost import (
    CostModel,
    GenerationBudget,
    GenerationKind,
    GenerationLedger,
    UpgradeDecision,
    UsdCost,
    estimate_tokens,
    expected_revenue_usd,
    plan_upgrades,
)
from app.modules.creative.domain.draft import CopyDraft, CopySource
from app.modules.creative.domain.guard import ViolationKind, check_copy
from app.modules.creative.domain.scoring import QualityScore, score_draft

logger = logging.getLogger(__name__)

# Budget estimate for one copy call before it runs: frozen system prompt + brief JSON in,
# four short lines + reasoning out.
EST_COPY_INPUT_TOKENS = 900
EST_COPY_OUTPUT_TOKENS = 220
COLD = Decimal("Infinity")
MEMO_ENTRIES = 1024
# The provider produced (and billed) tokens even though we could not use them.
BILLED_FAILURES = frozenset(
    {CopyFailure.INVALID_OUTPUT, CopyFailure.TIMEOUT}
)  # prewarm: worth it by definition, still under the hourly cap


class Outcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"  # failed the guard -> stock stays
    LOWER_SCORE = "lower_score"  # safe but not better than stock -> stock stays
    FAILED = "failed"  # provider error
    TIMEOUT = "timeout"
    BUDGET_DENIED = "budget_denied"
    DISABLED = "disabled"  # writer disabled for the session (auth/quota)


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationRecord:
    signature: str
    writer: str
    outcome: Outcome
    latency_ms: float
    cost: UsdCost
    ai_score: Decimal | None = None
    stock_score: Decimal | None = None
    violations: frozenset[ViolationKind] = frozenset()
    model_version: str | None = None
    failure: CopyFailure | None = None


@dataclass
class StudioStats:
    composed: int = 0
    cache_hits: int = 0
    upgrades_requested: int = 0
    outcomes: dict[Outcome, int] = field(default_factory=dict)
    records: deque[GenerationRecord] = field(default_factory=lambda: deque(maxlen=500))

    def count(self, record: GenerationRecord) -> None:
        self.outcomes[record.outcome] = self.outcomes.get(record.outcome, 0) + 1
        self.records.append(record)


@dataclass(frozen=True, slots=True, kw_only=True)
class Composed:
    brief: CreativeBrief
    draft: CopyDraft
    score: QualityScore
    compose_ms: float


@dataclass(frozen=True, slots=True, kw_only=True)
class StudioConfig:
    copy_timeout_s: float = 2.5
    expected_dwell_s: Decimal = Decimal(8)
    # The rubric measures targeting and safety, not wit: AI copy may trail stock's total by
    # this much, but never on a targeting part (TARGETING_PARTS).
    ai_tolerance: Decimal = Decimal("0.05")


TARGETING_PARTS = ("angle", "party", "brands", "price", "held")


def ai_wins(ai: QualityScore, stock: QualityScore, tolerance: Decimal) -> bool:
    """Whether a guarded AI draft may replace stock copy on air."""
    if not ai.verdict.passed:
        return False
    if any(ai.parts[p] < stock.parts[p] for p in TARGETING_PARTS):
        return False
    return ai.total >= stock.total - tolerance


class CreativeStudio:
    def __init__(
        self,
        *,
        clock: BaseClock,
        writer: CopyWriter | None = None,
        cost_model: CostModel | None = None,
        budget: GenerationBudget | None = None,
        ledger: GenerationLedger | None = None,
        cache: CopyCache | None = None,
        config: StudioConfig | None = None,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._clock = clock
        self._writer = writer
        self._stock = StockCopywriter()
        self.cost_model = cost_model or CostModel()
        self.budget = budget or GenerationBudget()
        self.ledger = ledger or GenerationLedger()
        self.cache = cache or CopyCache()
        self.config = config or StudioConfig()
        self._timer = timer
        self.disabled_by: CopyFailure | None = None
        self.stats = StudioStats()
        self._memo: OrderedDict[CreativeBrief, tuple[CopyDraft, QualityScore]] = OrderedDict()
        # compose() runs on worker threads (under the demo lock); upgrades run on the event
        # loop thread. One lock guards the memo, the cache and the ledger between them.
        self._lock = threading.RLock()

    @property
    def ai_enabled(self) -> bool:
        return self._writer is not None and self.disabled_by is None

    # ------------------------------------------------------------------ hot

    def compose(self, brief: CreativeBrief) -> Composed:
        started = self._timer()
        with self._lock:
            memo = self._memo.get(brief)
            if memo is None:
                memo = self._decide(brief)
                self._memo[brief] = memo
                while len(self._memo) > MEMO_ENTRIES:
                    self._memo.popitem(last=False)
            draft, score = memo
            if draft.source is not CopySource.STOCK:
                self.stats.cache_hits += 1
                self.cache.stats.hits += 1
                draft = draft.cached()
            self.stats.composed += 1
        return Composed(
            brief=brief, draft=draft, score=score, compose_ms=(self._timer() - started) * 1000
        )

    def _decide(self, brief: CreativeBrief) -> tuple[CopyDraft, QualityScore]:
        """Cached AI copy if it still passes the guard for this exact brief, else stock."""
        draft = self.cache.peek(brief.signature())
        if draft is not None:
            score = score_draft(draft, brief)
            if score.verdict.passed:
                return draft, score
        stock = self._stock.compose(brief)
        return stock, score_draft(stock, brief)

    def stock(self, brief: CreativeBrief) -> CopyDraft:
        return self._stock.compose(brief)

    # ------------------------------------------------------------------ warm

    def copy_estimate(self) -> UsdCost:
        return self.cost_model.copy(
            input_tokens=EST_COPY_INPUT_TOKENS, output_tokens=EST_COPY_OUTPUT_TOKENS
        )

    def plan_copy(self, brief: CreativeBrief, *, value_per_s_clp: Decimal) -> UpgradeDecision:
        revenue = (
            COLD
            if value_per_s_clp.is_infinite()
            else expected_revenue_usd(
                value_per_s_clp=value_per_s_clp,
                expected_dwell_s=self.config.expected_dwell_s,
                budget=self.budget,
            )
        )
        (decision,) = plan_upgrades(
            [(GenerationKind.COPY, self.copy_estimate())],
            expected_revenue=revenue,
            ledger=self.ledger,
            budget=self.budget,
            at=self._clock.now_utc(),
        )
        return decision

    def needs_upgrade(self, brief: CreativeBrief) -> bool:
        """Hot-path check: is there anything for the warm path to do?"""
        with self._lock:
            return self.ai_enabled and brief.signature() not in self.cache

    async def upgrade_copy(
        self, brief: CreativeBrief, *, value_per_s_clp: Decimal
    ) -> CopyDraft | None:
        signature = brief.signature()
        writer = self._writer
        self.stats.upgrades_requested += 1
        if writer is None or self.disabled_by is not None:
            self._record(signature, "none", Outcome.DISABLED, 0.0, UsdCost.zero())
            return None
        if (cached := self.cache.get(signature)) is not None:
            # Same signature, possibly a different brief (e.g. 7 vs 9 people): re-check.
            return cached if check_copy(cached, brief).passed else None
        decision = self.plan_copy(brief, value_per_s_clp=value_per_s_clp)
        if not decision.approved:
            self._record(signature, writer.name, Outcome.BUDGET_DENIED, 0.0, UsdCost.zero())
            return None

        started = self._timer()
        try:
            written = await asyncio.wait_for(writer.write(brief), self.config.copy_timeout_s)
        except TimeoutError:
            latency = (self._timer() - started) * 1000
            # A timed-out call may still be billed: book the estimate (reconcile, don't guess zero).
            self._spend(decision.estimate)
            self._record(signature, writer.name, Outcome.TIMEOUT, latency, decision.estimate)
            return None
        except CopyWriterError as exc:
            latency = (self._timer() - started) * 1000
            if exc.failure is CopyFailure.QUOTA:
                self.disabled_by = exc.failure  # stop spending calls that will all fail
            billed = exc.failure in BILLED_FAILURES
            if billed:
                self._spend(decision.estimate)
            self._record(
                signature,
                writer.name,
                Outcome.FAILED,
                latency,
                decision.estimate if billed else UsdCost.zero(),
                failure=exc.failure,
            )
            logger.warning("ai copy failed", extra={"source": exc.failure.value})
            return None
        except Exception:
            latency = (self._timer() - started) * 1000
            self._record(
                signature,
                writer.name,
                Outcome.FAILED,
                latency,
                UsdCost.zero(),
                failure=CopyFailure.UNAVAILABLE,
            )
            logger.exception("ai copy writer raised", extra={"source": writer.name})
            return None
        latency = (self._timer() - started) * 1000

        cost = self._usage_cost(written.usage, written.draft)
        self._spend(cost)
        ai = score_draft(written.draft, brief)
        stock = score_draft(self._stock.compose(brief), brief)
        if not ai.verdict.passed:
            outcome = Outcome.REJECTED
        elif not ai_wins(ai, stock, self.config.ai_tolerance):
            outcome = Outcome.LOWER_SCORE
        else:
            outcome = Outcome.ACCEPTED
            with self._lock:
                self.cache.put(signature, written.draft)
                self._memo.clear()  # briefs sharing this signature now air the AI draft
        self._record(
            signature,
            writer.name,
            outcome,
            latency,
            cost,
            ai_score=ai.total,
            stock_score=stock.total,
            violations=ai.verdict.kinds(),
            model_version=written.draft.model_version,
        )
        return written.draft if outcome is Outcome.ACCEPTED else None

    async def prewarm(self, briefs: Iterable[CreativeBrief], *, concurrency: int = 4) -> int:
        """Cold path: write and cache copy for expected briefs. Returns how many were cached."""
        unique = {b.signature(): b for b in briefs}
        gate = asyncio.Semaphore(concurrency)

        async def one(brief: CreativeBrief) -> bool:
            async with gate:
                return await self.upgrade_copy(brief, value_per_s_clp=COLD) is not None

        results = await asyncio.gather(*(one(b) for b in unique.values()))
        return sum(results)

    def record_spend(self, kind: GenerationKind, cost: UsdCost) -> None:
        with self._lock:
            self.ledger.record(kind, cost, at=self._clock.now_utc())

    # ------------------------------------------------------------------ internals

    def _usage_cost(self, usage: TokenUsage, draft: CopyDraft) -> UsdCost:
        if usage.input_tokens or usage.output_tokens:
            return self.cost_model.copy(
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens
            )
        return self.cost_model.copy(
            input_tokens=EST_COPY_INPUT_TOKENS,
            output_tokens=estimate_tokens(draft.all_text() + draft.reasoning),
        )

    def _spend(self, cost: UsdCost) -> None:
        if cost.micros:
            with self._lock:
                self.ledger.record(GenerationKind.COPY, cost, at=self._clock.now_utc())

    def _record(
        self,
        signature: str,
        writer: str,
        outcome: Outcome,
        latency_ms: float,
        cost: UsdCost,
        *,
        ai_score: Decimal | None = None,
        stock_score: Decimal | None = None,
        violations: frozenset[ViolationKind] = frozenset(),
        model_version: str | None = None,
        failure: CopyFailure | None = None,
    ) -> None:
        self.stats.count(
            GenerationRecord(
                signature=signature,
                writer=writer,
                outcome=outcome,
                latency_ms=latency_ms,
                cost=cost,
                ai_score=ai_score,
                stock_score=stock_score,
                violations=violations,
                model_version=model_version,
                failure=failure,
            )
        )
