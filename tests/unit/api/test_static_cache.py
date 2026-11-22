"""Campaign media is cacheable (browser + CDN) and health checks stay out of the log."""

import logging
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.static_files import MEDIA_CACHE_CONTROL
from app.config.logging_setup import SkipHealthChecks
from app.config.settings import AppSettings
from app.main import create_app


def _client(tmp_path: Path) -> TestClient:
    (tmp_path / "loops").mkdir()
    (tmp_path / "loops" / "demo.mp4").write_bytes(b"\x00" * 4096)
    settings = AppSettings(
        _env_file=None,
        app_env="test",
        media_dir=tmp_path,
        audience_ai=False,
        creative_ai_copy=False,
        voiceovers_enabled=False,
    )
    return TestClient(create_app(settings))


def test_campaign_assets_and_media_carry_one_cache_policy(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        for path in ("/campaign-assets/logos/monster.svg", "/media/loops/demo.mp4"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.headers["cache-control"] == MEDIA_CACHE_CONTROL, path


def test_a_revalidation_and_a_range_request_keep_the_policy(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        first = client.get("/media/loops/demo.mp4")
        again = client.get(
            "/media/loops/demo.mp4", headers={"If-None-Match": first.headers["etag"]}
        )
        assert again.status_code == 304
        assert again.headers["cache-control"] == MEDIA_CACHE_CONTROL
        part = client.get("/media/loops/demo.mp4", headers={"Range": "bytes=0-1023"})
        assert part.status_code == 206 and len(part.content) == 1024
        assert part.headers["cache-control"] == MEDIA_CACHE_CONTROL


def test_api_responses_are_not_made_cacheable(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        assert "public" not in client.get("/health").headers.get("cache-control", "")


def _access(path: str, status: int) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:1", "GET", path, "1.1", status),
        None,
    )


def test_a_passing_health_check_is_not_logged_but_a_failing_one_and_others_are() -> None:
    skip = SkipHealthChecks()
    assert not skip.filter(_access("/health", 200))
    assert skip.filter(_access("/health", 503))
    assert skip.filter(_access("/campaign-assets/logos/monster.svg", 200))
