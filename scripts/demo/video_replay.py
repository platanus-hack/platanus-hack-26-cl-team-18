"""Run the vision agent on a stock eval clip and show its live ad beside the source."""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
import uvicorn
from evals.video.fetch import clip_path, load_clips
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse
from starlette.routing import Route

ROOT = Path(__file__).resolve().parents[2]
EDGE = ROOT / "edge"

_PAGE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Video to live ad</title>
<style>
body {{ max-width: 1500px; margin: auto; padding: 24px; background: #121612;
       color: #f0f4ee; font: 1rem system-ui, sans-serif; }}
h1, h2 {{ margin: 0 0 .65rem; }} h2 {{ font-size: 1rem; }}
main {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }}
video, iframe {{ display: block; width: 100%; aspect-ratio: 16 / 9;
                background: #080a08; border: 0; border-radius: .5rem; }}
a {{ color: #d2efca; }} figure {{ margin: 0; }} figcaption {{ margin-top: .65rem; }}
@media (max-width: 760px) {{ main {{ grid-template-columns: 1fr; }} }}
</style>
<h1>Video to live ad</h1>
<p>{scene} The browser and the vision agent loop the clip independently.</p>
<main>
  <figure>
    <h2>Source video</h2>
    <video src="/source" autoplay loop muted playsinline controls></video>
    <figcaption><a href="{source}">{artist}</a> · <a href="{license_url}">{license}</a></figcaption>
  </figure>
  <section>
    <h2>Live ad</h2>
    <iframe src="{player}" title="Live ad player" allow="autoplay"></iframe>
  </section>
</main>
<p><a href="{console}" target="_blank" rel="noopener">Open the live auction console</a></p>
</html>
"""


def create_review(
    clip: dict[str, str], *, video: Path, player_origin: str, screen: str, command: list[str]
) -> Starlette:
    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        agent = subprocess.Popen(command, cwd=EDGE)
        app.state.agent = agent
        try:
            yield
        finally:
            if agent.poll() is None:
                agent.terminate()
                try:
                    agent.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    agent.kill()
                    agent.wait()

    async def index(request: Request) -> HTMLResponse:
        status = request.app.state.agent.poll()
        if status is not None:
            return HTMLResponse(
                f"The vision agent exited with status {status}. See the launcher terminal.",
                status_code=503,
            )
        return HTMLResponse(
            _PAGE.format(
                scene=escape(clip["scene"]),
                source=escape(clip["page"]),
                artist=escape(clip["artist"]),
                license_url=escape(clip["license_url"]),
                license=escape(clip["license"]),
                player=escape(f"{player_origin}/screen?{urlencode({'id': screen})}"),
                console=escape(f"{player_origin}/console?{urlencode({'screen': screen})}"),
            )
        )

    async def media(_request: Request) -> FileResponse:
        return FileResponse(video)

    return Starlette(routes=[Route("/", index), Route("/source", media)], lifespan=lifespan)


def local_origin(raw: str) -> str:
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise argparse.ArgumentTypeError("expected a local HTTP origin")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def main() -> None:
    clips = {clip["id"]: clip for clip in load_clips()}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clip", choices=clips, default="coffee-counter")
    parser.add_argument("--player", type=local_origin, default="http://127.0.0.1:5173")
    parser.add_argument("--backend", type=local_origin, default="http://127.0.0.1:8000")
    parser.add_argument("--port", type=int, default=5189)
    args = parser.parse_args()
    clip = clips[args.clip]
    original = clip_path(clip)
    preview = original.with_suffix(".preview.webm")
    edge_python = EDGE / ".venv" / "bin" / "python"
    if not original.is_file():
        parser.error("clip is missing. Run make eval-video-fetch.")
    if not edge_python.is_file():
        parser.error("vision is missing. Run uv sync --extra vision from edge/.")
    try:
        for origin in {args.player, args.backend}:
            health = httpx.get(f"{origin}/health", timeout=3, trust_env=False)
            health.raise_for_status()
            if not health.json().get("ads", {}).get("enabled"):
                parser.error(f"the ad loop is disabled at {origin}")
    except httpx.HTTPError, ValueError:
        parser.error("start the local backend and web player before launching the replay")
    screen = f"replay-{args.clip}"
    backend_ws = args.backend.replace("http", "ws", 1) + "/ws/agent"
    command = [
        str(edge_python),
        "-m",
        "agent.vision",
        "--screen",
        screen,
        "--backend",
        backend_ws,
        "--video",
        str(original),
        "--device",
        "cpu",
        "--no-keyframes",
    ]
    app = create_review(
        clip,
        video=preview if preview.is_file() else original,
        player_origin=args.player,
        screen=screen,
        command=command,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
