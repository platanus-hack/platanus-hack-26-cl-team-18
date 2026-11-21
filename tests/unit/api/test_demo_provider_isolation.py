"""Developer credentials must not turn an offline demo/eval into paid provider calls."""

import asyncio
import sys

import pytest
import scripts.station as launcher
from scripts.station import provider_environment

from app.bootstrap.container import build_container
from app.config.settings import AppSettings


def test_default_station_overrides_credentials_even_when_dotenv_contains_keys(
    tmp_path, monkeypatch
):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "ANTHROPIC_API_KEY=fake-anthropic\nELEVENLABS_API_KEY=fake-elevenlabs\n"
        "ELEVENLABS_NARRATOR_VOICE_ID=fake-voice\nFAL_KEY=fake-fal\n"
        "AUDIENCE_AI=true\nCREATIVE_AI_COPY=true\nVOICEOVERS_ENABLED=true\n"
        'VOICEOVER_PREWARM=true\nVLM_ENABLED=true\nSPEECH_MODE_SCREENS=["demo-01"]\n'
    )
    environment = provider_environment({"ELEVENLABS_API_KEY": "inherited-fake"}, paid=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    settings = AppSettings(_env_file=dotenv, app_env="local", media_dir=tmp_path / "media")
    container = build_container(settings)
    try:
        assert (
            container.voiceovers
            is container.tagger
            is container.copywriter
            is container.vlm
            is None
        )
        assert not settings.voiceover_prewarm
        assert settings.speech_mode_screens == []
        assert not settings.fal_key.get_secret_value()
        assert not settings.elevenlabs_api_key.get_secret_value()
        assert not settings.anthropic_api_key.get_secret_value()
    finally:
        asyncio.run(container.aclose())


def test_paid_providers_need_an_explicit_launcher_option():
    original = {"ELEVENLABS_API_KEY": "fake-key", "AUDIENCE_AI": "true"}
    assert provider_environment(original, paid=True) == original
    assert original["ELEVENLABS_API_KEY"] == "fake-key"


def test_speech_without_paid_opt_in_exits_before_starting_processes(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["live_demo", "--speech"])
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code == 2
    assert "--paid-providers" in capsys.readouterr().err


def test_test_environment_never_builds_real_voice_or_vision_adapters(monkeypatch, tmp_path):
    import app.bootstrap.container as composition

    def forbidden(*args, **kwargs):
        pytest.fail("a test attempted to construct a real paid adapter")

    monkeypatch.setattr(composition, "ElevenLabsVoiceSynthesizer", forbidden)
    monkeypatch.setattr(composition, "build_llm_provider", forbidden)
    settings = AppSettings(
        _env_file=None,
        app_env="test",
        media_dir=tmp_path,
        elevenlabs_api_key="fake-key",
        elevenlabs_narrator_voice_id="fake-voice",
        anthropic_api_key="fake-key",
        voiceovers_enabled=True,
        vlm_enabled=True,
    )
    container = build_container(settings)
    assert container.voiceovers is container.vlm is None
