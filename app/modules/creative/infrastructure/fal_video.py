"""fal.ai text-to-video behind `VideoGenerator` (cold path, queue API, capped by the caller).

The model id is configuration (`FAL_VIDEO_MODEL`); verify the endpoint on day 1, as
architecture §8 says. Never logs the API key.
"""

from __future__ import annotations

import asyncio

import httpx

from app.modules.creative.application.ports.video import VideoFailure, VideoGenerationError

QUEUE = "https://queue.fal.run"


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
                json={"prompt": prompt, "duration": str(duration_s)},
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
