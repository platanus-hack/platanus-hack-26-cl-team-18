"""fal.ai text-to-video behind `VideoGenerator` (cold path, queue API, capped by the caller).

The model id is configuration (`FAL_VIDEO_MODEL`, default `DEFAULT_VIDEO_MODEL`: the
open-weights LTX-2 19B distilled). fal's video models disagree on the request shape,
so `request_body` builds it per model family; an unknown family gets the generic
`{prompt, duration}` body. Never logs the API key.
"""

from __future__ import annotations

import asyncio

import httpx

from app.modules.creative.application.ports.video import VideoFailure, VideoGenerationError

QUEUE = "https://queue.fal.run"
DEFAULT_VIDEO_MODEL = "fal-ai/ltx-2-19b/distilled/text-to-video"

_LTX2_FPS = 25
# Replaces fal's default negative prompt, so it keeps the quality terms and adds
# what invariant 4 forbids inside generated video (logos and text are overlays).
_LTX2_NEGATIVE = (
    "blurry, out of focus, overexposed, underexposed, low contrast, distorted, jittery, "
    "text, letters, words, subtitles, logos, labels, watermark, brand names, people, faces"
)


def request_body(model: str, prompt: str, *, duration_s: int) -> dict[str, object]:
    """The queue submit body for `model`. Pure, so every family is unit-tested."""
    if model.startswith("fal-ai/ltx-2-19b/"):
        # No `duration` input: length is num_frames / fps, and LTX wants 8n + 1 frames.
        frames = 8 * max(1, round(duration_s * _LTX2_FPS / 8)) + 1
        return {
            "prompt": prompt,
            "negative_prompt": _LTX2_NEGATIVE,
            "num_frames": frames,
            "fps": _LTX2_FPS,
            "video_size": "landscape_16_9",  # the screen is a 16:9 TV
            "generate_audio": False,  # loops play muted under our own voice-over
            # Our prompt is already guarded ("no text, no logos"); an LLM rewrite
            # could put brand names or lettering back in.
            "enable_prompt_expansion": False,
            "video_output_type": "X264 (.mp4)",
        }
    return {"prompt": prompt, "duration": str(duration_s)}


class FalVideoGenerator:
    name = "fal"

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        client: httpx.AsyncClient | None = None,
        poll_s: float = 2.0,
        timeout_s: float = 600.0,
    ) -> None:
        if not api_key:
            raise VideoGenerationError(VideoFailure.NOT_CONFIGURED)
        self.model = model
        self._client = client or httpx.AsyncClient(timeout=60)
        self._headers = {"Authorization": f"Key {api_key}"}
        self._poll_s = poll_s
        self._timeout_s = timeout_s

    async def aclose(self) -> None:
        await self._client.aclose()

    async def generate(self, prompt: str, *, duration_s: int) -> bytes:
        try:
            submitted = await self._client.post(
                f"{QUEUE}/{self.model}",
                headers=self._headers,
                json=request_body(self.model, prompt, duration_s=duration_s),
            )
            if submitted.status_code in (400, 422):
                raise VideoGenerationError(VideoFailure.REJECTED, str(submitted.status_code))
            submitted.raise_for_status()
            body = submitted.json()
            status_url, response_url = body["status_url"], body["response_url"]
            waited = 0.0
            while True:
                status = (await self._client.get(status_url, headers=self._headers)).json()
                if status.get("status") == "COMPLETED":
                    break
                if waited >= self._timeout_s:
                    raise VideoGenerationError(VideoFailure.TIMEOUT)
                await asyncio.sleep(self._poll_s)
                waited += self._poll_s
            result = (await self._client.get(response_url, headers=self._headers)).json()
            video = (await self._client.get(result["video"]["url"])).content
        except VideoGenerationError:
            raise
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise VideoGenerationError(VideoFailure.UNAVAILABLE, type(exc).__name__) from exc
        if not video:
            raise VideoGenerationError(VideoFailure.INVALID_OUTPUT)
        return video
