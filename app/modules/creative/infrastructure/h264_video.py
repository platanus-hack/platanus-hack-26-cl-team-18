"""Normalize generated media for the kiosk before it enters the loop cache."""

import asyncio
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from app.modules.creative.application.ports.video import (
    VideoFailure,
    VideoGenerationError,
    VideoGenerator,
)


class H264VideoGenerator:
    def __init__(self, inner: VideoGenerator) -> None:
        self._inner = inner
        self.name, self.model = inner.name, inner.model

    async def generate(self, prompt: str, *, duration_s: int) -> bytes:
        if shutil.which("ffmpeg") is None:
            raise VideoGenerationError(
                VideoFailure.NOT_CONFIGURED, "install ffmpeg before generation"
            )
        source = await self._inner.generate(prompt, duration_s=duration_s)
        # Generated MP4s may put their index at the end, so ffmpeg needs a
        # seekable input. This temporary file contains generated media only.
        with TemporaryDirectory(prefix="team18-video-") as tmp:
            input_path = Path(tmp) / "source.mp4"
            input_path.write_bytes(source)
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-loglevel",
                "error",
                "-i",
                str(input_path),
                "-an",
                "-t",
                str(duration_s),
                "-vf",
                "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "fast",
                "-movflags",
                "frag_keyframe+empty_moov",
                "-f",
                "mp4",
                "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                output, _ = await asyncio.wait_for(process.communicate(), timeout=60)
            except TimeoutError as exc:
                raise VideoGenerationError(
                    VideoFailure.INVALID_OUTPUT, "transcode timeout"
                ) from exc
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            if process.returncode != 0 or not output:
                raise VideoGenerationError(VideoFailure.INVALID_OUTPUT, "transcode failed")
            return output
