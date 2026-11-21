"""Offline evals must stay offline on a fully configured developer laptop."""

import evals.live as live
import httpx


def test_eval_backend_does_not_inherit_provider_credentials(monkeypatch, tmp_path):
    for key, value in {
        "ANTHROPIC_API_KEY": "fake-anthropic",
        "ELEVENLABS_API_KEY": "fake-elevenlabs",
        "ELEVENLABS_NARRATOR_VOICE_ID": "fake-narrator",
        "FAL_KEY": "fake-fal",
        "VLM_ENABLED": "true",
        "VOICEOVER_PREWARM": "true",
    }.items():
        monkeypatch.setenv(key, value)
    original = live.build_container

    def checked_container(settings, **kwargs):
        assert settings.elevenlabs_api_key is None
        assert settings.anthropic_api_key is None
        assert settings.fal_key is None
        assert not settings.vlm_enabled
        return original(settings, **kwargs)

    monkeypatch.setattr(live, "build_container", checked_container)
    with live.live_backend(media_dir=tmp_path) as backend:
        providers = httpx.get(f"{backend.http_url}/health").json()["ads"]["providers"]
        assert not any(provider["configured"] for provider in providers.values())
