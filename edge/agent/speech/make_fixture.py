"""Create the benchmark fixture ONCE: TTS audio (pcm_16000) + per-character timings.

    cd edge && uv run python -m agent.speech.make_fixture

Spends ~60 TTS characters (about 30 credits on Flash) the first time; refuses to
overwrite an existing fixture, so it can never spend twice by accident.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

from agent.speech.bench import FIXTURES

TEXT = "Uf, qué hambre. ¿Vamos por un café después del gimnasio?"
VOICE = os.environ.get("ELEVENLABS_NARRATOR_VOICE_ID") or "JBFqnCBsd6RMkjVDRZzb"


def main() -> int:
    meta_path, pcm_path = FIXTURES / "bench_line.json", FIXTURES / "bench_line_pcm16k.raw"
    if meta_path.exists() and pcm_path.exists():
        print("fixture exists: nothing spent")
        return 0
    from agent.speech.__main__ import _load_dotenv

    _load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    _load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        print("ELEVENLABS_API_KEY is not set", file=sys.stderr)
        return 2
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}/with-timestamps?output_format=pcm_16000",
        data=json.dumps(
            {"text": TEXT, "model_id": "eleven_flash_v2_5", "language_code": "es"}
        ).encode(),
        headers={"xi-api-key": key, "content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # fixed https host
        payload = json.loads(resp.read())
        cost = resp.headers.get("character-cost")
    FIXTURES.mkdir(parents=True, exist_ok=True)
    pcm_path.write_bytes(base64.b64decode(payload["audio_base64"]))
    meta = {
        "text": TEXT,
        "voice_id": VOICE,
        "model_id": "eleven_flash_v2_5",
        "alignment": payload["alignment"],
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"fixture written ({pcm_path.stat().st_size} bytes); character-cost header: {cost}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
