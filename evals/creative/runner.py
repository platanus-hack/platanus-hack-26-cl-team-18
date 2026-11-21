"""Creative generation eval (docs/creative-generation.md §4).

Drives the real studio (brief → hot compose → warm guarded upgrade → aired copy)
over a labelled suite and scores four families: targeting, safety, quality,
latency + cost. Offline modes are deterministic (stock, recorded fixtures);
`--writer claude` runs the same suite live and spends real money.

python -m evals creative                      # recorded writer (CI mode)
python -m evals creative --writer stock       # stock templates only
python -m evals creative --writer claude      # live Claude Haiku 5.5 (needs ANTHROPIC_API_KEY)
python -m evals creative --writer claude --record   # live + overwrite recordings.json
python -m evals creative --update-baseline    # lock improved ratchets
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from app.modules.ai.domain.model_capabilities import ModelProfile
from app.modules.audience.domain.value_objects.segment import (
    SegmentEstimate,
    SegmentId,
    SegmentSource,
)
from app.modules.campaigns.domain.book import BrandId, BrandKit
from app.modules.common.domain.value_objects.money import Money
from app.modules.common.domain.value_objects.percentage import Percentage
from app.modules.common.infrastructure.clock.system_clock import SystemClock
from app.modules.creative.application.briefing import (
    CreativeCatalog,
    MomentContext,
    assemble_brief,
)
from app.modules.creative.application.ports.copywriter import CopyWriter, WrittenCopy
from app.modules.creative.application.rendering import render_spec
from app.modules.creative.application.studio import CreativeStudio, Outcome, StudioConfig
from app.modules.creative.domain.angle import Angle
from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.draft import CopyDraft, CopySource
from app.modules.creative.domain.guard import ViolationKind, check_copy
from app.modules.creative.domain.lexicon import KNOWN_BRANDS, contains_term, normalize
from app.modules.creative.domain.offer import BasketOffer, OfferBook
from app.modules.creative.infrastructure.recorded_copywriter import (
    RecordedCopywriter,
    Recording,
)

HERE = Path(__file__).resolve().parent
CASES_FILE = HERE / "cases.json"
RECORDINGS_FILE = HERE / "recordings.json"
ADVERSARIAL_FILE = HERE / "adversarial.json"
BASELINE_FILE = HERE / "baseline.json"
HARVEST_FILE = HERE / "harvested.json"


class HarvestingWriter:
    """Feedback loop: remembers every draft a writer returns, keyed by brief signature.

    The studio keeps only the violation kinds of a rejected draft; this keeps its
    text, so a live run's rejected or demoted outputs can become eval cases
    (`--harvest` → harvested.json → promote to adversarial.json with an `expect`).
    """

    def __init__(self, inner: CopyWriter) -> None:
        self._inner = inner
        self.drafts: dict[str, CopyDraft] = {}

    @property
    def name(self) -> str:
        return self._inner.name

    async def write(self, brief: CreativeBrief) -> WrittenCopy:
        written = await self._inner.write(brief)
        self.drafts[brief.signature()] = written.draft
        return written


RESULTS_DIR = HERE.parent / "results"

WriterKind = Literal["stock", "recorded", "claude"]
HOT_COMPOSE_P95_MS = 5.0
WARM_COPY_P95_MS = 2500.0
QUALITY_MIN = 0.6
DEFAULT_VALUE_PER_S_CLP = Decimal(20)


# ------------------------------------------------------------------ suite


@dataclass(frozen=True, slots=True, kw_only=True)
class Case:
    id: str
    beat: str
    basket: tuple[str, ...]
    looking: int
    people: int
    segments: tuple[SegmentEstimate, ...]
    held: tuple[str, ...]
    queue: int
    minor: bool
    expect: Mapping[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class Suite:
    catalog: CreativeCatalog
    ids: Mapping[str, BrandId]
    categories: Mapping[str, str]
    cases: tuple[Case, ...]

    def brief(self, case: Case) -> CreativeBrief:
        return assemble_brief(
            basket=tuple((self.ids[n], self.categories[n]) for n in case.basket),
            layout="duo" if len(case.basket) == 2 else "solo",
            segments=case.segments,
            looking=case.looking,
            people=case.people,
            minor_likely=case.minor,
            catalog=self.catalog,
            context=MomentContext(
                held_brand_ids=frozenset(self.ids[n] for n in case.held),
                queue_length=case.queue,
            ),
        )


def _segment(raw: Mapping[str, Any]) -> SegmentEstimate:
    speech = raw.get("speech")
    return SegmentEstimate.create(
        segment_id=SegmentId.create(str(raw["id"])),
        viewer_count=int(raw["viewers"]),
        confidence=Percentage.create(int(raw.get("confidence", 90))),
        sources=frozenset({SegmentSource.SPEECH if speech is not None else SegmentSource.VISION}),
        speech_intent=Percentage.create(int(speech)) if speech is not None else None,
    )


def load_suite(path: Path = CASES_FILE) -> Suite:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_brands = data["catalog"]["brands"]
    ids = {
        b["name"]: BrandId.create(f"00000000-0000-7000-8000-{i + 1:012d}")
        for i, b in enumerate(raw_brands)
    }
    kits = {
        ids[b["name"]]: BrandKit(
            brand_id=ids[b["name"]],
            name=b["name"],
            logo_url=f"/media/logos/{b['name']}.png",
            stock_copy=b["stock_copy"],
            accent_color="#c6f26b",
        )
        for b in raw_brands
    }
    offers = OfferBook.of(
        BasketOffer(
            brand_ids=frozenset(ids[n] for n in o["brands"]),
            min_party=int(o["min_party"]),
            label=o["label"],
            text=o["text"],
            price=Money.create(currency="CLP", amount=int(o["price"])),
        )
        for o in data["catalog"]["offers"]
    )
    catalog = CreativeCatalog(
        kits=kits,
        offers=offers,
        segment_angles={
            SegmentId.create(k): Angle(v) for k, v in data["catalog"]["segment_angles"].items()
        },
    )
    cases = tuple(
        Case(
            id=c["id"],
            beat=c.get("beat", ""),
            basket=tuple(c["basket"]),
            looking=int(c["looking"]),
            people=int(c.get("people", c["looking"])),
            segments=tuple(_segment(s) for s in c.get("segments", [])),
            held=tuple(c.get("held", [])),
            queue=int(c.get("queue", 0)),
            minor=bool(c.get("minor", False)),
            expect=c.get("expect", {}),
        )
        for c in data["cases"]
    )
    return Suite(
        catalog=catalog,
        ids=ids,
        categories={b["name"]: b["category"] for b in raw_brands},
        cases=cases,
    )


def load_recordings(path: Path = RECORDINGS_FILE) -> dict[str, Recording]:
    data = json.loads(path.read_text(encoding="utf-8"))
    version = str(data.get("model_version", "recorded"))
    return {
        case_id: Recording.from_json({"model_version": version, **raw})
        for case_id, raw in data["recordings"].items()
    }


# ------------------------------------------------------------------ checks


def has_text(text: str, term: str, *, prefix: bool = False) -> bool:
    """`a|b` = any alternative; symbol-only terms ("$") match as plain substrings.

    `prefix=True` (used for must_not) also matches inflections: "cerveza" hits "cervezas".
    """
    for option in term.split("|"):
        if not any(ch.isalnum() for ch in option):
            if option in text:
                return True
        elif contains_term(normalize(text), option + ("*" if prefix else "")):
            return True
    return False


# ------------------------------------------------------------------ independent oracle
# Deliberately NOT the guard: a second, simpler implementation over the aired copy, so a
# gap in the guard cannot hide behind the guard's own verdict (docs/evals.md "oracles").

_ORACLE_ALCOHOL = (
    "cervez",
    "chela",
    "birra",
    "alcohol",
    "vino",
    "pisco",
    "whisk",
    "vodka",
    "copete",
    "trago",
    "tequila",
    "fernet",
    "beer",
    "wine",
)


def _squash(text: str) -> str:
    return "".join(ch for ch in normalize(text) if ch.isalnum())


def oracle_violations(brief: CreativeBrief, draft: CopyDraft, catalog_names: set[str]) -> list[str]:
    text = " \n".join((draft.teaser, draft.offer, draft.close, draft.voice_line))
    found: list[str] = []
    price = brief.offer.price_text if brief.offer else None
    price_digits = re.sub(r"\D", "", price) if price else None
    for token in re.findall(r"\d[\d.,]*\d|\d{3,}", text):
        digits = re.sub(r"\D", "", token)
        if len(digits) >= 3 and digits != price_digits:
            found.append(f"amount:{token}")
    if price and price not in draft.offer:
        found.append("offer-without-price")
    squashed = _squash(text)
    basket = {_squash(n) for n in brief.names}
    for name in catalog_names:
        key = _squash(name)
        if key not in basket and len(key) >= 4 and key in squashed:
            found.append(f"brand:{name}")
    if brief.minor_likely and any(w in squashed for w in _ORACLE_ALCOHOL):
        found.append("alcohol-with-minor")
    mentioned = {int(m) for m in re.findall(r"\blos (\d+)\b", normalize(text))}
    if mentioned - ({brief.addressed_party} if brief.addressed_party else set()):
        found.append(f"party:{sorted(mentioned)}")
    return found


@dataclass
class CaseResult:
    id: str
    beat: str
    angle: str
    angle_ok: bool
    offer_ok: bool
    party_named: bool | None  # None when the party is 1
    contains_ok: int
    contains_total: int
    must_not_hits: list[str]
    aired_violations: list[str]
    quality: float
    source: str
    outcome: str | None
    hot_ms: float
    warm_ms: float | None
    teaser: str
    offer: str
    close: str
    oracle_violations: list[str] = field(default_factory=list)
    render_error: str | None = None


def _visible(draft: CopyDraft) -> str:
    return " \n".join((draft.teaser, draft.offer, draft.close, draft.voice_line))


def check_case(case: Case, brief: CreativeBrief, draft: CopyDraft) -> dict[str, Any]:
    expect = case.expect
    text = _visible(draft)
    angle_ok = expect.get("angle") is None or brief.angle.angle.value == expect["angle"]
    offer_ok = True
    if "offer" in expect:
        offer_ok = brief.offer is not None and brief.offer.label == expect["offer"]
    if "price" in expect:
        offer_ok = (
            offer_ok
            and brief.offer is not None
            and brief.offer.price_text == expect["price"]
            and expect["price"] in draft.offer
        )
    musts = list(expect.get("must_contain", []))
    hits = [t for t in expect.get("must_not", []) if has_text(text, t, prefix=True)]
    headline = f"{draft.teaser} {draft.offer}"
    named = None
    if brief.is_group:
        if brief.offer_undersized:
            # A 4-pack for 12 is addressed honestly: offered to share, never "para todo el grupo".
            named = has_text(headline, "compartir") and not has_text(
                headline, "todo el grupo|para todos|cada uno"
            )
        elif brief.addressed_party:
            named = has_text(
                headline, f"los {brief.addressed_party}|{brief.addressed_party} personas"
            )
        else:
            named = has_text(headline, "grupo|todos")
    return {
        "angle_ok": angle_ok,
        "offer_ok": offer_ok,
        "party_named": named,
        "contains_ok": sum(1 for t in musts if has_text(text, t)),
        "contains_total": len(musts),
        "must_not_hits": hits,
    }


# ------------------------------------------------------------------ run


class MissingRecording(LookupError):
    """Recorded mode needs a recording per case that calls the writer."""


def build_writer(kind: WriterKind) -> CopyWriter | None:
    if kind == "stock":
        return None
    if kind == "recorded":
        return RecordedCopywriter({})
    from app.modules.ai.infrastructure.adapters.llm.factory import build_llm_provider
    from app.modules.creative.infrastructure.claude_copywriter import ClaudeCopywriter

    return ClaudeCopywriter(build_llm_provider(), ModelProfile("claude-haiku-5-5", "low"))


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


@dataclass
class Report:
    writer: str
    metrics: dict[str, float | None]
    cases: list[CaseResult]
    adversarial: list[dict[str, Any]]
    outcomes: dict[str, int]
    recorded: dict[str, dict[str, Any]] = field(default_factory=dict)
    harvested: list[dict[str, Any]] = field(default_factory=list)


async def run(
    kind: WriterKind = "recorded",
    *,
    suite: Suite | None = None,
    recordings: Mapping[str, Recording] | None = None,
    writer: CopyWriter | None = None,
    harvest: bool = False,
) -> Report:
    suite = suite or load_suite()
    if writer is None:
        writer = build_writer(kind)
    harvester = HarvestingWriter(writer) if harvest and writer is not None else None
    if harvester is not None:
        writer = harvester
    harvested: list[dict[str, Any]] = []
    if kind == "recorded" and recordings is None:
        recordings = load_recordings()
    studio = CreativeStudio(
        clock=SystemClock(), writer=writer, config=StudioConfig(copy_timeout_s=10.0)
    )
    # Warm up like a running server (regex compilation, imports) before timing the hot path.
    warmup = CreativeStudio(clock=SystemClock())
    for case in suite.cases:
        warmup.compose(suite.brief(case))
    catalog_names = {kit.name for kit in suite.catalog.kits.values()} | set(KNOWN_BRANDS)
    results: list[CaseResult] = []
    warm: list[float] = []
    captured: dict[str, dict[str, Any]] = {}

    for case in suite.cases:
        brief = suite.brief(case)
        started = time.perf_counter()
        studio.compose(brief)
        hot_ms = (time.perf_counter() - started) * 1000

        outcome: str | None = None
        warm_ms: float | None = None
        if writer is not None and studio.needs_upgrade(brief):
            recording = (recordings or {}).get(case.id)
            if isinstance(writer, RecordedCopywriter):
                if recording is None:
                    raise MissingRecording(case.id)  # never let a gap pass as "fell back"
                writer.put(brief.signature(), recording)
            before = len(studio.stats.records)
            await studio.upgrade_copy(brief, value_per_s_clp=DEFAULT_VALUE_PER_S_CLP)
            if len(studio.stats.records) > before:
                record = studio.stats.records[-1]
                outcome = record.outcome.value
                draft = harvester.drafts.get(brief.signature()) if harvester else None
                if draft is not None and record.outcome in (Outcome.REJECTED, Outcome.LOWER_SCORE):
                    harvested.append(
                        {
                            "case": case.id,
                            "outcome": record.outcome.value,
                            "violations": sorted(v.value for v in record.violations),
                            "teaser": draft.teaser,
                            "offer": draft.offer,
                            "close": draft.close,
                            "voice_line": draft.voice_line,
                            "model_version": record.model_version,
                            "writer": record.writer,
                        }
                    )
                warm_ms = (
                    recording.latency_ms
                    if isinstance(writer, RecordedCopywriter) and recording is not None
                    else record.latency_ms
                )
                warm.append(warm_ms)
            if not isinstance(writer, RecordedCopywriter):
                cached = studio.cache.get(brief.signature())
                if cached is not None:
                    captured[case.id] = {
                        "teaser": cached.teaser,
                        "offer": cached.offer,
                        "close": cached.close,
                        "voice_line": cached.voice_line,
                        "latency_ms": round(warm_ms or 0),
                    }

        aired = studio.compose(brief)
        verdict = check_copy(aired.draft, brief)
        oracle = oracle_violations(brief, aired.draft, catalog_names)
        try:
            render_spec(brief, aired.draft, suite.catalog.kits)  # the wire contract must accept it
            render_error = None
        except Exception as exc:
            render_error = type(exc).__name__
        check = check_case(case, brief, aired.draft)
        results.append(
            CaseResult(
                id=case.id,
                beat=case.beat,
                angle=brief.angle.angle.value,
                quality=float(aired.score.total),
                source=aired.draft.source.value,
                outcome=outcome,
                hot_ms=hot_ms,
                warm_ms=warm_ms,
                aired_violations=[v.kind.value for v in verdict.violations],
                oracle_violations=oracle,
                render_error=render_error,
                teaser=aired.draft.teaser,
                offer=aired.draft.offer,
                close=aired.draft.close,
                **check,
            )
        )

    # Second pass: what the hot path now actually serves from cache.
    second_pass_hits = sum(
        1
        for case in suite.cases
        if studio.compose(suite.brief(case)).draft.source is not CopySource.STOCK
    )

    adversarial = run_adversarial(suite)
    n = len(results)
    groups = [r for r in results if r.party_named is not None]
    contains_total = sum(r.contains_total for r in results)
    ai_records = [
        r for r in studio.stats.records if r.ai_score is not None and r.stock_score is not None
    ]
    outcomes = {o.value: c for o, c in studio.stats.outcomes.items()}
    attempted = sum(outcomes.values())
    total_cost = studio.ledger.total()

    metrics: dict[str, float | None] = {
        "angle_accuracy": sum(r.angle_ok for r in results) / n,
        "offer_accuracy": sum(r.offer_ok for r in results) / n,
        "party_mention_rate": (sum(bool(r.party_named) for r in groups) / len(groups))
        if groups
        else 1.0,
        "must_contain_rate": (sum(r.contains_ok for r in results) / contains_total)
        if contains_total
        else 1.0,
        "must_not_violations": float(sum(len(r.must_not_hits) for r in results)),
        "aired_guard_violations": float(sum(len(r.aired_violations) for r in results)),
        "oracle_violations": float(sum(len(r.oracle_violations) for r in results)),
        "render_errors": float(sum(r.render_error is not None for r in results)),
        "adversarial_rejection_rate": (
            sum(a["rejected"] for a in adversarial) / len(adversarial) if adversarial else 1.0
        ),
        "fallback_rate": (
            sum(c for o, c in outcomes.items() if o != Outcome.ACCEPTED.value) / attempted
            if attempted
            else None
        ),
        "ai_air_rate": sum(r.source != CopySource.STOCK.value for r in results) / n,
        "ai_win_rate": (
            sum(r.outcome is Outcome.ACCEPTED for r in ai_records) / len(ai_records)
            if ai_records
            else None
        ),
        "quality_mean": sum(r.quality for r in results) / n,
        "quality_min": min(r.quality for r in results),
        "hot_compose_p95_ms": _percentile([r.hot_ms for r in results], 0.95),
        "warm_copy_p50_ms": _percentile(warm, 0.5),
        "warm_copy_p95_ms": _percentile(warm, 0.95),
        "cache_hit_rate": second_pass_hits / n,
        "cost_total_usd": float(total_cost.dollars),
        "cost_per_creative_usd": float(total_cost.dollars) / n,
        "cost_per_1k_moments_usd": float(total_cost.dollars) / n * 1000,
        "budget_denials": float(outcomes.get(Outcome.BUDGET_DENIED.value, 0)),
    }
    return Report(
        writer=kind,
        metrics=metrics,
        cases=results,
        adversarial=adversarial,
        outcomes=outcomes,
        recorded=captured,
        harvested=harvested,
    )


def save_harvest(report: Report, path: Path = HARVEST_FILE) -> int:
    """Append new rejected/demoted drafts (deduplicated by text); returns how many were new."""
    data: dict[str, Any] = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else {
            "_comment": (
                "Live outputs the studio rejected or demoted (`python -m evals creative "
                "--writer claude --harvest`). Review each one: a real violation becomes an "
                "adversarial.json entry with its `expect` kind; a false rejection becomes a "
                "guard fix plus a benign test. Never edit by hand except to mark `reviewed`."
            ),
            "entries": [],
        }
    )
    seen = {(e["case"], e["teaser"], e["offer"], e["close"]) for e in data["entries"]}
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    new = 0
    for entry in report.harvested:
        key = (entry["case"], entry["teaser"], entry["offer"], entry["close"])
        if key in seen:
            continue
        seen.add(key)
        data["entries"].append({**entry, "harvested_at": stamp, "reviewed": False})
        new += 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return new


def run_adversarial(suite: Suite, path: Path = ADVERSARIAL_FILE) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    by_id = {c.id: c for c in suite.cases}
    out = []
    for entry in data["entries"]:
        brief = suite.brief(by_id[entry["case"]])
        draft = CopyDraft(
            teaser=entry["teaser"],
            offer=entry["offer"],
            close=entry["close"],
            voice_line=entry["voice_line"],
            source=CopySource.AI,
        )
        kinds = check_copy(draft, brief).kinds()
        expected = ViolationKind(entry["expect"])
        out.append(
            {
                "case": entry["case"],
                "expect": expected.value,
                "found": sorted(k.value for k in kinds),
                "rejected": expected in kinds,
            }
        )
    return out


# ------------------------------------------------------------------ scorecard


@dataclass(frozen=True, slots=True)
class Spec:
    kind: Literal["gate", "ratchet", "info"]
    better: Literal["higher", "lower"] = "higher"
    limit: float | None = None
    label: str = ""


SPECS: dict[str, Spec] = {
    "angle_accuracy": Spec("gate", "higher", 1.0, "Angle accuracy"),
    "offer_accuracy": Spec("gate", "higher", 1.0, "Offer (party size + price) accuracy"),
    "must_not_violations": Spec("gate", "lower", 0, "Forbidden words aired"),
    "aired_guard_violations": Spec("gate", "lower", 0, "Guard violations aired"),
    "oracle_violations": Spec("gate", "lower", 0, "Independent oracle violations aired"),
    "render_errors": Spec("gate", "lower", 0, "Spec render errors (hot-path crash)"),
    "adversarial_rejection_rate": Spec("gate", "higher", 1.0, "Adversarial outputs rejected"),
    "quality_min": Spec("gate", "higher", QUALITY_MIN, "Quality, worst aired"),
    "hot_compose_p95_ms": Spec("gate", "lower", HOT_COMPOSE_P95_MS, "Hot compose p95 (ms)"),
    "warm_copy_p95_ms": Spec("gate", "lower", WARM_COPY_P95_MS, "Warm copy p95 (ms)"),
    "party_mention_rate": Spec("ratchet", "higher", label="Groups addressed by size"),
    "must_contain_rate": Spec("ratchet", "higher", label="Required words present"),
    "quality_mean": Spec("ratchet", "higher", label="Quality, mean aired"),
    "cache_hit_rate": Spec("ratchet", "higher", label="Cache hit rate (2nd pass)"),
    "cost_per_creative_usd": Spec("ratchet", "lower", label="Cost per creative (USD)"),
    "ai_air_rate": Spec("info", label="AI copy on air"),
    "ai_win_rate": Spec("info", label="AI beat stock"),
    "fallback_rate": Spec("info", "lower", label="Fallback rate"),
    "warm_copy_p50_ms": Spec("info", "lower", label="Warm copy p50 (ms)"),
    "cost_total_usd": Spec("info", "lower", label="Total spend (USD)"),
    "cost_per_1k_moments_usd": Spec("info", "lower", label="Cost per 1k moments (USD)"),
    "budget_denials": Spec("info", "lower", label="Budget denials"),
}
TOLERANCE = 1e-9


def judge(
    metrics: Mapping[str, float | None],
    baseline: Mapping[str, float] | None,
    *,
    writer: str = "stock",
) -> list[dict[str, Any]]:
    # With an AI writer every gate must have a value: "n/a" would hide a broken path.
    required = {k for k, v in SPECS.items() if v.kind == "gate"} if writer != "stock" else set()
    rows = []
    for key, spec in SPECS.items():
        value = metrics.get(key)
        base = (baseline or {}).get(key)
        status = "info"
        if spec.kind == "gate":
            if value is None:
                status = "FAIL" if key in required else "n/a"
            elif spec.limit is not None:
                ok = value >= spec.limit if spec.better == "higher" else value <= spec.limit
                status = "pass" if ok else "FAIL"
        elif spec.kind == "ratchet":
            if value is None or base is None:
                status = "new"
            else:
                delta = value - base if spec.better == "higher" else base - value
                status = (
                    "better" if delta > TOLERANCE else "same" if delta >= -TOLERANCE else ("WORSE")
                )
        rows.append(
            {
                "metric": key,
                "label": spec.label,
                "kind": spec.kind,
                "value": value,
                "baseline": base,
                "status": status,
            }
        )
    return rows


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    if abs(value) < 0.01 and value != 0:
        return f"{value:.6f}"
    return (
        f"{value:.3f}".rstrip("0").rstrip(".") if not float(value).is_integer() else str(int(value))
    )


def render(report: Report, rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# Creative eval: `{report.writer}` writer",
        "",
        f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC · {len(report.cases)} cases · "
        f"{len(report.adversarial)} adversarial outputs · outcomes {report.outcomes or '{}'}",
        "",
        "| Metric | Kind | Value | Baseline | Status |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['label']} | {r['kind']} | {_fmt(r['value'])} | {_fmt(r['baseline'])} | "
            f"{r['status']} |"
        )
    lines += [
        "",
        "## Aired copy per case",
        "",
        "| Case | Angle | Src | Q | Teaser | Offer |",
        "|---|---|---|---|---|---|",
    ]
    for c in report.cases:
        flag = "" if c.angle_ok and c.offer_ok and not c.must_not_hits else " ⚠"
        lines.append(
            f"| {c.id}{flag} | {c.angle} | {c.source}{'/' + c.outcome if c.outcome else ''} | "
            f"{c.quality:.2f} | {c.teaser} | {c.offer} |"
        )
    missed = [a for a in report.adversarial if not a["rejected"]]
    if missed:
        lines += ["", "## Adversarial outputs NOT rejected", ""]
        lines += [f"- {a['case']}: expected `{a['expect']}`, found {a['found']}" for a in missed]
    return "\n".join(lines) + "\n"


def failed(rows: list[dict[str, Any]]) -> list[str]:
    return [r["metric"] for r in rows if r["status"] in {"FAIL", "WORSE"}]


def load_baseline(writer: str, path: Path = BASELINE_FILE) -> dict[str, float] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get(writer)


def save_baseline(report: Report, path: Path = BASELINE_FILE) -> None:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[report.writer] = {
        k: v
        for k, v in report.metrics.items()
        if SPECS.get(k) is not None and SPECS[k].kind == "ratchet" and v is not None
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def save_recordings(report: Report, path: Path = RECORDINGS_FILE) -> None:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.setdefault("recordings", {}).update(report.recorded)
    data["model_version"] = "claude-haiku-5-5@low"
    data["_comment"] = "Recorded with `python -m evals creative --writer claude --record`."
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_results(report: Report, markdown: str, out_dir: Path = RESULTS_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    latest = out_dir / "creative-latest.md"
    latest.write_text(markdown, encoding="utf-8")
    (out_dir / "creative-latest.json").write_text(
        json.dumps(
            {
                "writer": report.writer,
                "metrics": report.metrics,
                "outcomes": report.outcomes,
                "cases": [c.__dict__ for c in report.cases],
                "adversarial": report.adversarial,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return latest
