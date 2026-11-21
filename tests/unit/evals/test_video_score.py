import json
from datetime import UTC, datetime

from evals.harness import RunRecord
from evals.metrics import Decision
from evals.video.run import score

from app.contract.messages import CreativeReady, CreativeSlot, CreativeSpec, LatencyTrace

T0 = datetime(2026, 11, 21, tzinfo=UTC)


def creative(brand: str | None) -> CreativeReady:
    return CreativeReady(
        screen_id="demo-01",
        creative_id=f"c-{brand}",
        auction_id=f"a-{brand}" if brand else None,
        spec=CreativeSpec(
            layout_id="solo",
            duration_ms=8000,
            slots=[
                CreativeSlot(
                    brand_id="b", brand_name=brand or "house", logo_url="l", copy_text=brand or "hi"
                )
            ],
        ),
        trace=LatencyTrace(received_at=T0, ready_at=T0),
    )


def test_timeline_scores_fit_reach_and_must_never_seconds(tmp_path) -> None:
    recording = tmp_path / "recording.agent.jsonl"
    snaps = [(0.0, []), (2.0, ["cup"]), (6.0, ["cup"]), (8.0, [])]
    recording.write_text(
        "".join(
            json.dumps(
                {
                    "t_s": t,
                    "snapshot": {
                        "people": 1,
                        "looking": 1,
                        "objects": [{"label": o, "score": 0.9} for o in objs],
                    },
                }
            )
            + "\n"
            for t, objs in snaps
        )
    )
    recording.with_suffix(".meta.json").write_text(json.dumps({"sample_fps": 0.5}))
    run = RunRecord(source="t", initial=creative(None))
    run.decisions = [
        Decision(
            source="t", step=1, minor_likely=False, creative=creative("Café Patio"), auction=None
        ),
        Decision(
            source="t",
            step=2,
            minor_likely=False,
            creative=creative("Café Patio"),
            auction=None,
            swapped=False,
        ),
        Decision(
            source="t", step=3, minor_likely=False, creative=creative("Malta Sur"), auction=None
        ),
    ]
    clip = {"id": "x", "expect": {"sensible_brands": ["Café Patio"], "never_brands": ["Malta Sur"]}}

    result = score(clip, recording, run)

    assert [(i.start_s, i.end_s, sorted(i.brands)) for i in result.timeline] == [
        (0.0, 2.0, []),
        (2.0, 8.0, ["Café Patio"]),
        (8.0, 10.0, ["Malta Sur"]),
    ]
    assert result.metrics["paid_share"] == 0.8
    assert result.metrics["ad_precision"] == 0.75  # 6 s fitting of 8 s paid
    assert result.metrics["inappropriate_ad_seconds"] == 2.0  # the gate that must stay 0
    assert result.metrics["published_fact_changes"] == 2
    assert result.unmapped == ["cup"]  # seen half the time, no segment ever understood
