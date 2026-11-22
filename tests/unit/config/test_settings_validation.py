"""A misconfigured env fails at boot with the field's name, never at the first auction."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config.settings import AppSettings


def _settings(**env: str) -> AppSettings:
    return AppSettings(_env_file=None, **env)  # type: ignore[arg-type]


@pytest.mark.parametrize("limits", [{"claude": 0}, {"fal": -1}])
def test_a_warm_provider_limit_below_one_fails_at_boot(limits: dict[str, int]) -> None:
    # Semaphore(0) would park every warm job forever: no AI copy, no upgrades, no error.
    with pytest.raises(ValidationError, match="warm_concurrency"):
        _settings(warm_concurrency=limits)  # type: ignore[arg-type]


def test_an_unknown_log_level_fails_at_boot_not_in_the_lifespan() -> None:
    with pytest.raises(ValidationError, match="log_level"):
        _settings(log_level="verbose")
    assert _settings(log_level="debug").log_level == "DEBUG"


@pytest.mark.parametrize("url", ["tunnel.example.com", "ftp://x.example", "https://", ""])
def test_the_qr_origin_must_be_an_http_origin(url: str) -> None:
    with pytest.raises(ValidationError, match="public_base_url"):
        _settings(public_base_url=url)


def test_the_qr_origin_drops_a_trailing_slash() -> None:
    assert _settings(public_base_url="https://x.example/").public_base_url == "https://x.example"


def test_render_external_url_is_the_qr_origin_when_none_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Render sets RENDER_EXTERNAL_URL on every web service; the backend serves /r/{code}.
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://team18.onrender.com")
    assert AppSettings(_env_file=None).public_base_url == "https://team18.onrender.com"  # type: ignore[call-arg]
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://tunnel.example")
    assert AppSettings(_env_file=None).public_base_url == "https://tunnel.example"  # type: ignore[call-arg]


def test_prod_warns_about_a_demo_key_too_short_to_unlock_anything() -> None:
    warnings = _settings(app_env="prod", demo_key="short", public_base_url="https://x.io")
    assert any("DEMO_KEY" in w for w in warnings.misconfigurations())
    ok = _settings(app_env="prod", demo_key="k" * 32, public_base_url="https://x.io")
    assert not any("DEMO_KEY" in w for w in ok.misconfigurations())


def test_a_loopback_qr_origin_is_reported_because_phones_cannot_open_it() -> None:
    warnings = _settings(public_base_url="http://localhost:5173").misconfigurations()
    assert any("PUBLIC_BASE_URL" in w for w in warnings)
    assert not any(
        "PUBLIC_BASE_URL" in w
        for w in _settings(public_base_url="https://abc.trycloudflare.com").misconfigurations()
    )
