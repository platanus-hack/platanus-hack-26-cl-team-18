"""One command for the local station: `make up` (`make up ARGS="--paid-providers"`).

Prepares whatever is missing, then hands over to `scripts/station.py` with ARGS.
Every step is idempotent and takes well under a second when already done, so
`make up` is also the everyday way to start:

1. `.env`: created from `.env.example` if missing (records in memory, no
   Postgres needed); a `DEMO_KEY` is generated if there is none.
2. Dependencies: backend venv, the perception agent with its camera and audio
   extras (`uv sync` is exact, so they are always requested together), and web
   packages when `package-lock.json` changed.
3. The YOLOE object vocabulary, baked once into `edge/`.
4. Postgres, only when `DATABASE_URL` is set: started with Docker if it is local
   and down, then migrated.
5. The provider preflight (informational), then the station.

Secrets are never printed: the demo key stays in `.env`.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
EDGE = ROOT / "edge"
WEB = ROOT / "web"
EDGE_EXTRAS = ("dev", "vision", "audio")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


# ------------------------------------------------------------------ pure helpers


def env_from_example(example: str) -> str:
    """A fresh `.env`: records stay in memory until someone opts into Postgres."""
    return re.sub(r"(?m)^DATABASE_URL=.*$", "DATABASE_URL=", example)


def ensure_demo_key(env_text: str, token: str) -> tuple[str, bool]:
    """Fill an empty or missing `DEMO_KEY`; a key already set is never replaced."""
    match = re.search(r"(?m)^DEMO_KEY=(.*)$", env_text)
    if match is not None and match.group(1).strip().strip("\"'"):
        return env_text, False
    if match is not None:
        return env_text[: match.start()] + f"DEMO_KEY={token}" + env_text[match.end() :], True
    separator = "" if env_text.endswith("\n") or not env_text else "\n"
    return f"{env_text}{separator}DEMO_KEY={token}\n", True


def env_value(env_text: str, name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(name)}=(.*)$", env_text)
    return match.group(1).strip().strip("\"'") if match else ""


def needs_npm_ci(web: Path) -> bool:
    """npm records the installed tree in node_modules/.package-lock.json."""
    installed = web / "node_modules" / ".package-lock.json"
    lock = web / "package-lock.json"
    return not installed.is_file() or lock.stat().st_mtime > installed.stat().st_mtime


def database_target(url: str) -> tuple[str, int] | None:
    if not url:
        return None
    parsed = urlparse(url)
    return parsed.hostname or "localhost", parsed.port or 5432


# ------------------------------------------------------------------ steps


def _step(message: str) -> None:
    print(f"→ {message}", flush=True)


def _run(
    command: Sequence[str],
    *,
    cwd: Path = ROOT,
    quiet: bool = True,
    extra_env: dict[str, str] | None = None,
) -> None:
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}  # each venv is its own
    env.update(extra_env or {})
    result = subprocess.run(
        list(command),
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE if quiet else None,
        stderr=subprocess.STDOUT if quiet else None,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        if quiet and result.stdout:
            print(result.stdout[-4000:], file=sys.stderr)
        raise SystemExit(f"✗ failed: {' '.join(command)}")


def require_tools() -> None:
    missing = [tool for tool in ("uv", "npm") if shutil.which(tool) is None]
    if missing:
        raise SystemExit(f"✗ install first: {', '.join(missing)}")


def prepare_env() -> str:
    env_file = ROOT / ".env"
    if not env_file.exists():
        _step("creating .env from .env.example (records in memory; no keys yet)")
        env_file.write_text(env_from_example((ROOT / ".env.example").read_text()))
    text = env_file.read_text()
    text, added = ensure_demo_key(text, secrets.token_urlsafe(16))
    if added:
        env_file.write_text(text)
        _step("generated DEMO_KEY in .env (the demo hub is /demo/<DEMO_KEY>)")
    return text


def sync_dependencies() -> None:
    _step("backend dependencies")
    _run(["uv", "sync", "--extra", "dev"])
    _step("perception agent dependencies (camera + audio)")
    extras = [arg for extra in EDGE_EXTRAS for arg in ("--extra", extra)]
    _run(["uv", "sync", *extras], cwd=EDGE)
    if needs_npm_ci(WEB):
        _step("web dependencies (npm ci)")
        _run(["npm", "ci", "--no-audit", "--no-fund"], cwd=WEB)


def bake_vocabulary() -> None:
    python = str(EDGE / ".venv/bin/python")
    probe = (
        "from agent.vision.adapters import DEFAULT_OBJECT_MODEL as m;"
        "from agent.vision.vocabulary import baked_weights_path as p;"
        "print(p(m))"
    )
    baked = subprocess.run(
        [python, "-c", probe], cwd=EDGE, capture_output=True, text=True, check=True
    ).stdout.strip()
    if not (EDGE / baked).is_file():
        _step("baking the YOLOE object vocabulary (once; downloads MobileCLIP)")
        _run([python, "-m", "agent.vision.vocabulary"], cwd=EDGE, quiet=False)


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def prepare_database(env_text: str) -> None:
    url = os.environ.get("DATABASE_URL", env_value(env_text, "DATABASE_URL"))
    target = database_target(url)
    if target is None:
        return  # in memory: nothing to start
    host, port = target
    if not _reachable(host, port):
        if host not in LOCAL_HOSTS or shutil.which("docker") is None:
            raise SystemExit(
                f"✗ DATABASE_URL points at {host}:{port}, which is down. Start it, "
                "or blank DATABASE_URL in .env to keep records in memory."
            )
        _step("starting local Postgres (docker compose)")
        _run(["docker", "compose", "up", "-d", "--wait", "postgres"])
    _step("database migrations")
    # migrate.py reads DATABASE_URL from the environment only (Render's pre-deploy).
    _run(["uv", "run", "python", "scripts/migrate.py"], extra_env={"DATABASE_URL": url})


def main(argv: Sequence[str] | None = None) -> int:
    station_args = list(sys.argv[1:] if argv is None else argv)
    require_tools()
    env_text = prepare_env()
    sync_dependencies()
    bake_vocabulary()
    prepare_database(env_text)
    _step("provider preflight")
    subprocess.run([sys.executable, "-m", "scripts.live_preflight"], cwd=ROOT, check=False)
    _step("starting the station (Ctrl-C stops everything)")
    os.chdir(ROOT)
    os.execv(sys.executable, [sys.executable, "-m", "scripts.station", *station_args])
    return 0  # unreachable: execv replaces this process


if __name__ == "__main__":
    raise SystemExit(main())
