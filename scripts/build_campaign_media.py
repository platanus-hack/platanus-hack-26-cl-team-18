"""Build compact, seamless H.264 photo-motion loops from the bundled product photography.

Cold, local and reproducible. No provider calls, text, logos, prices or QR codes
are baked into the loop. Existing fal generation remains available for product motion.
"""

import argparse
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default="ffmpeg", help="Path to the local ffmpeg binary")
    parser.add_argument(
        "photos", nargs="*", help="Photo stems to build (default: every photo); others are kept"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "assets" / "campaigns"
    (root / "videos").mkdir(exist_ok=True)
    wanted = set(args.photos)
    for photo in sorted((root / "photos").glob("*.webp")):
        if wanted and photo.stem not in wanted:
            continue
        output = root / "videos" / f"{photo.stem}.mp4"
        subprocess.run(
            [
                args.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(photo),
                "-vf",
                "zoompan=z='1.025+0.012*sin(2*PI*on/143)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=144:s=1280x720:fps=24,format=yuv420p",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "24",
                "-movflags",
                "+faststart",
                str(output),
            ],
            check=True,
        )
        print(f"{output.relative_to(root)}: {output.stat().st_size // 1024} KiB")


if __name__ == "__main__":
    main()
