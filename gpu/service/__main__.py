"""Run the service: `uv run python -m service --port 8080` (env config: see service.app).

Binds loopback by default (a local dev GPU). A GPU box on the venue LAN or a VM
binds `--host 0.0.0.0` and must set `GPU_TOKEN` (or, on an isolated LAN only,
`GPU_ALLOW_ANONYMOUS=1`); without either it refuses to start.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn

from service.app import AnonymousExposure, check_exposure
from service.protocol import MAX_MESSAGE_BYTES

logger = logging.getLogger("gpu.service")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="service")
    parser.add_argument(
        "--host", default="127.0.0.1", help="0.0.0.0 for a LAN box or VM (needs GPU_TOKEN)"
    )
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(name)s %(message)s")
    try:
        check_exposure(args.host, os.environ.get("GPU_TOKEN") or None)
    except AnonymousExposure as exc:
        logger.error("%s", exc)
        sys.exit(2)
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
