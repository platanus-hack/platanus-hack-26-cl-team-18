from evals.campaigns import CAMPAIGN_ASSETS, evaluate_campaigns


def test_real_campaign_catalog_passes_quality_safety_media_and_latency_gates() -> None:
    report = evaluate_campaigns(gate_latency=False)
    assert report["passed"], report["failures"]
    assert report["campaigns"] == 576
    assert report["rejections"] == 156
    assert report["cache_hit_rate"] == 1
    assert report["video_assets"] == 6  # + Rayo's lavender can
    # every pre-generated loop has a poster photo; photo-only heroes (the Chile catalog) are fine
    assert report["photo_assets"] >= report["video_assets"]
    videos = {path.stem for path in (CAMPAIGN_ASSETS / "videos").glob("*.mp4")}
    photos = {path.stem for path in (CAMPAIGN_ASSETS / "photos").glob("*.webp")}
    assert videos <= photos, videos - photos
