"""Pre-generated bundle loops: hot-path lookup, capped cold generation, fal adapter."""

import json
from decimal import Decimal
from pathlib import Path

import anyio
import httpx
import pytest

from app.modules.creative.application.loops import (
    BundleLoopLibrary,
    LoopJob,
    bundle_key,
    loop_prompt,
)
from app.modules.creative.application.ports.video import VideoFailure, VideoGenerationError
from app.modules.creative.infrastructure.fal_video import FalVideoGenerator

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


class FakeGenerator:
    name, model = "fake", "fake-v1"

    def __init__(self, *, fail: set[str] | None = None, garbage: bool = False) -> None:
        self.prompts: list[str] = []
        self.fail = fail or set()
        self.garbage = garbage

    async def generate(self, prompt: str, *, duration_s: int) -> bytes:
        self.prompts.append(prompt)
        if any(word in prompt for word in self.fail):
            raise VideoGenerationError(VideoFailure.UNAVAILABLE)
        return b"not a video" if self.garbage else MP4


def jobs() -> list[LoopJob]:
    return [
        LoopJob(slugs=("voltio", "cafe-patio"), categories=("sports_goods", "coffee")),
        LoopJob(slugs=("rayo", "cafe-patio"), categories=("sports_goods", "coffee")),
        LoopJob(slugs=("malta-sur",), categories=("alcohol",)),
    ]


def test_prompts_ask_for_motion_and_never_text_or_logos() -> None:
    prompt = loop_prompt(["coffee", "pastry"])
    assert "No text" in prompt and "no logos" in prompt and "no people" in prompt
    assert bundle_key(["voltio", "cafe-patio"]) == bundle_key(["cafe-patio", "voltio"])


def test_generation_is_capped_by_budget_cached_and_atomic(tmp_path: Path) -> None:
    library = BundleLoopLibrary(tmp_path)
    generator = FakeGenerator()
    report = anyio.run(
        lambda: library.generate_missing(
            generator, jobs(), cost_per_clip_usd=Decimal("0.5"), budget_usd=Decimal("1")
        )
    )
    assert report.generated == ("cafe-patio+voltio", "cafe-patio+rayo")
    assert report.skipped_budget == ("malta-sur",) and report.spent_usd == Decimal("1")
    meta = json.loads(library.path_for(["voltio", "cafe-patio"]).with_suffix(".json").read_text())
    assert meta["model_version"] == "fake:fake-v1" and len(meta["prompt_hash"]) == 16
    assert not list(tmp_path.rglob("*.part"))
    again = anyio.run(
        lambda: library.generate_missing(
            generator, jobs(), cost_per_clip_usd=Decimal("0.5"), budget_usd=Decimal("5")
        )
    )
    assert again.cached == ("cafe-patio+voltio", "cafe-patio+rayo") and again.generated == (
        "malta-sur",
    )


def test_failures_and_garbage_never_become_loops(tmp_path: Path) -> None:
    library = BundleLoopLibrary(tmp_path)
    report = anyio.run(
        lambda: library.generate_missing(
            FakeGenerator(garbage=True),
            jobs()[:1],
            cost_per_clip_usd=Decimal("0.1"),
            budget_usd=Decimal("1"),
        )
    )
    assert report.failed == (("cafe-patio+voltio", "invalid_output"),)
    assert library.url_for(["voltio", "cafe-patio"]) is None


def test_the_hot_path_finds_the_bundle_loop_or_the_lead_brands_solo(tmp_path: Path) -> None:
    library = BundleLoopLibrary(tmp_path)
    assert library.url_for(["voltio", "cafe-patio"]) is None
    library.path_for(["voltio"]).parent.mkdir(parents=True)
    library.path_for(["voltio"]).write_bytes(MP4)
    assert library.url_for(["voltio", "cafe-patio"]) == "/media/loops/voltio.mp4"
    library.path_for(["voltio", "cafe-patio"]).write_bytes(MP4)
    assert library.url_for(["voltio", "cafe-patio"]) == "/media/loops/cafe-patio+voltio.mp4"


def fal(handler):  # type: ignore[no-untyped-def]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FalVideoGenerator("key", model="fal-ai/some/text-to-video", client=client, poll_s=0)


def test_fal_queue_submit_poll_and_download() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        assert request.url.host != "queue.fal.run" or request.headers["authorization"] == "Key key"
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "status_url": "https://queue.fal.run/s",
                    "response_url": "https://queue.fal.run/r",
                },
            )
        if request.url.path == "/s":
            return httpx.Response(200, json={"status": "COMPLETED"})
        if request.url.path == "/r":
            return httpx.Response(200, json={"video": {"url": "https://cdn.fal.media/clip.mp4"}})
        return httpx.Response(200, content=MP4)

    clip = anyio.run(lambda: fal(handler).generate("steam", duration_s=6))
    assert clip == MP4 and seen == ["/fal-ai/some/text-to-video", "/s", "/r", "/clip.mp4"]


@pytest.mark.parametrize(
    ("response", "failure"),
    [
        (httpx.Response(422, json={}), VideoFailure.REJECTED),
        (httpx.Response(500, json={}), VideoFailure.UNAVAILABLE),
    ],
)
def test_fal_errors_are_typed(response: httpx.Response, failure: VideoFailure) -> None:
    generator = fal(lambda request: response)
    with pytest.raises(VideoGenerationError) as caught:
        anyio.run(lambda: generator.generate("steam", duration_s=6))
    assert caught.value.failure is failure


def test_fal_needs_a_key() -> None:
    with pytest.raises(VideoGenerationError):
        FalVideoGenerator("", model="m")
