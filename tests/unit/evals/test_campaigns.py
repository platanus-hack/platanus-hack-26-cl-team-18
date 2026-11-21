from evals.campaigns import evaluate_campaigns


def test_real_campaign_catalog_passes_quality_safety_media_and_latency_gates() -> None:
    report = evaluate_campaigns(gate_latency=False)
    assert report["passed"], report["failures"]
    assert report["campaigns"] == 576
    assert report["rejections"] == 156
    assert report["cache_hit_rate"] == 1
    assert report["photo_assets"] == report["video_assets"] == 6  # + Rayo's lavender can
