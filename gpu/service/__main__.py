"""Run the service: `uv run python -m service --port 8080` (env config: see service.app)."""

from __future__ import annotations

import argparse
import logging

import uvicorn

from service.protocol import MAX_MESSAGE_BYTES


def main() -> None:
    parser = argparse.ArgumentParser(prog="service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(name)s %(message)s")
    uvicorn.run(
        "service.app:app",
        host=args.host,
        port=args.port,
        workers=1,  # one model per GPU; frames never queue across workers
        log_level=args.log_level,
        access_log=False,  # no per-frame logging
        ws_max_size=MAX_MESSAGE_BYTES,
        ws_per_message_deflate=False,  # JPEGs don't compress; deflate only adds latency
        ws_ping_interval=5.0,
        ws_ping_timeout=5.0,
    )


if __name__ == "__main__":
    main()
