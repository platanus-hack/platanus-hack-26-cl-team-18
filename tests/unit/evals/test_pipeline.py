"""The pipeline journey eval: its analysis math, and one short live journey."""

from decimal import Decimal

from evals.pipeline import JourneyRun, MomentTrace, ProviderLog, analyse, run_journey


def moment(i: int, brands: tuple[str, ...], ready: float, copy: float | None, **kw) -> MomentTrace:
    m = MomentTrace(index=i, scene="s", sent=100.0 + i * 10, paid=bool(brands), brands=brands)
    m.ready_ms, m.copy_ms, m.server_ms = ready, copy, 2.0
    m.voice_ms = m.complete_ms = copy
    m.first_voice_ms = kw.get("voice", copy)
    m.receipt_total = kw.get("billed", Decimal("10") if brands else None)
    m.attention_s, m.expected_attention_s = Decimal("4"), 4.0 if brands else 0.0
    return m


def test_analysis_finds_the_bottleneck_cache_effect_and_billing_gates() -> None:
    copy_log = ProviderLog()
    copy_log.add("sig-a", 100.0 + 0.01, 600.0)  # moment 0's provider call: 10 ms in, 600 ms long
    run = JourneyRun(
        moments=[
            moment(0, ("A",), ready=10, copy=640),
            moment(1, (), ready=5, copy=None),
            moment(2, ("A",), ready=12, copy=12),  # same group again: served from cache
        ],
        copy_log=copy_log,
        voice_log=ProviderLog(),
        duration_s=30,
        config={},
    )
    result = analyse(run)
    metrics = result["metrics"]
    assert result["bottleneck"] == "copy wait"
    assert metrics["first_complete_ad_p50_ms"] == 640
    assert metrics["repeat_complete_ad_p50_ms"] == 12
    assert round(metrics["copy_pipeline_overhead_ms"]) == 30  # 640 - (10 + 600): our share
    assert metrics["house_ad_billed"] == 0
    assert metrics["billing_receipt_rate"] == 1


def test_house_ad_billing_is_caught() -> None:
    run = JourneyRun(
        moments=[moment(0, (), ready=5, copy=None, billed=Decimal("3"))],
        copy_log=ProviderLog(),
        voice_log=ProviderLog(),
        duration_s=5,
        config={},
    )
    assert analyse(run)["metrics"]["house_ad_billed"] == 1


def test_warmed_demo_journey_serves_the_complete_ad_from_cache() -> None:
    """Live: full generation stack, sandbox TTS, warm-up, then a two-moment journey."""
    run = run_journey(dwell_s=1.5, journey=("co-brand", "fitness"), warmup=True)
    metrics = analyse(run)["metrics"]
    assert metrics["pipeline_paid_moments"] == 2
    assert metrics["complete_ad_p95_ms"] is not None and metrics["complete_ad_p95_ms"] < 300
    assert metrics["copy_provider_calls"] == 0 and metrics["voice_provider_calls"] == 0
    assert metrics["voice_duplicate_syntheses"] == 0
    assert metrics["billing_receipt_rate"] == 1 and metrics["house_ad_billed"] == 0
