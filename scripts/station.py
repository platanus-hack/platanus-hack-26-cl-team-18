"""One local station: one backend URL for camera, speech and player, with owned restarts.

Defaults to local perception and stock ads, without paid providers.
Only processes started here are stopped/restarted. Audio/transcripts are never saved.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.request import urlopen

from app.config.settings import AppSettings

ROOT = Path(__file__).resolve().parents[1]


def provider_environment(environment: dict[str, str], *, paid: bool) -> dict[str, str]:
    """Explicit empty env values override keys in .env; removing them would reload them."""
    result = dict(environment)
    if not paid:
        result.update(
            ANTHROPIC_API_KEY="",
            ELEVENLABS_API_KEY="",
            FAL_KEY="",
            AUDIENCE_AI="false",
            CREATIVE_AI_COPY="false",
            VOICEOVERS_ENABLED="false",
            VOICEOVER_PREWARM="false",
            VLM_ENABLED="false",
            SPEECH_MODE_SCREENS="[]",
        )
    return result


def live_switch_environment(environment: dict[str, str], *, start_live: bool) -> dict[str, str]:
    """The station starts in standby: nothing is sensed or generated until the console's
    "Iniciar en vivo" button (app/realtime/live_switch.py). `--start-live` skips it."""
    return {**environment, "LIVE_STARTS_ON": "true" if start_live else "false"}


def agent_live_args(*, start_live: bool) -> list[str]:
    """Agents in a standby station wait for the switch before opening the camera loop or mic."""
    return [] if start_live else ["--await-live"]


@dataclass
class ManagedProcess:
    name: str
    command: list[str]
    cwd: Path
    env: dict[str, str]
    log: BinaryIO
    process: subprocess.Popen | None = None
    restart_at: float = 0
    restarts: int = 0

    def tick(self, now: float) -> None:
        if self.process is not None:
            code = self.process.poll()
            if code is None:
                return
            # npm's shell may exit while its Vite child is still alive.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGTERM)
            self.restarts += 1
            self.restart_at = now + min(5, self.restarts)
            print(
                f"{self.name} exited ({code}); restarting in {min(5, self.restarts)}s", flush=True
            )
            self.process = None
        if now >= self.restart_at:
            self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=self.env,
                stdout=self.log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            print(f"{self.name} started (pid {self.process.pid})", flush=True)

    def stop(self) -> None:
        if self.process is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=5188)
    parser.add_argument("--screen", default="demo-01")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument(
        "--paid-providers",
        action="store_true",
        help="explicitly enable configured paid AI providers",
    )
    parser.add_argument(
        "--speech", action="store_true", help="opt-in speech; ADR 0005 disclosure applies"
    )
    parser.add_argument("--silero-model", type=Path)
    parser.add_argument(
        "--start-live",
        action="store_true",
        help="skip standby: sense and generate from startup (default: wait for the console)",
    )
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    if args.speech and not args.paid_providers:
        parser.error("--speech requires --paid-providers; default rehearsals never spend credits")
    if (
        not args.screen
        or len(args.screen) > 64
        or any(not (c.isascii() and (c.isalnum() or c in "_.:-")) for c in args.screen)
    ):
        parser.error("invalid screen id")
    if args.backend_port == args.web_port or any(
        not 1 <= p <= 65535 for p in (args.backend_port, args.web_port)
    ):
        parser.error("backend and web need distinct valid ports")
    os.chdir(ROOT)
    settings = AppSettings()
    edge_python = ROOT / "edge/.venv/bin/python"
    if not edge_python.is_file():
        parser.error("install the edge environment with vision/audio extras first")
    if args.speech and (
        not settings.elevenlabs_api_key or not settings.elevenlabs_api_key.get_secret_value()
    ):
        parser.error("speech needs ELEVENLABS_API_KEY in the root .env")
    if args.speech and (args.silero_model is None or not args.silero_model.is_file()):
        parser.error("speech needs --silero-model pointing to a downloaded ONNX model")
    # Refuse to attach to an unrelated backend, or kill another task's process.
    for port in (args.backend_port, args.web_port):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                parser.error(
                    f"port {port} is occupied; stop that station or choose different ports"
                )
    backend = f"http://127.0.0.1:{args.backend_port}"
    origin = f"http://127.0.0.1:{args.web_port}"
    link = backend.replace("http://", "ws://") + "/ws/agent"
    env = live_switch_environment(
        provider_environment(dict(os.environ), paid=args.paid_providers),
        start_live=args.start_live,
    )
    env["BACKEND_URL"] = backend
    # An inherited build override must not send this station to a different backend.
    env["VITE_BACKEND_URL"] = origin
    env["SPEECH_MODE_SCREENS"] = json.dumps([args.screen] if args.speech else [])
    # The console's "Lo que ve la cámara": detection boxes only (no image ever leaves
    # the agent). The backend relays them only to /ws/dev, behind DEMO_KEY.
    env["EDGE_DEV_STREAM"] = "1"
    lib = ROOT / "var/local-demo/lib/usr"
    if lib.is_dir():
        env["PATH"] = f"{lib / 'bin'}:{env.get('PATH', '')}"
        env["LD_LIBRARY_PATH"] = f"{lib / 'lib/x86_64-linux-gnu'}:{env.get('LD_LIBRARY_PATH', '')}"
    commands = [
        (
            "backend",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.backend_port),
                "--no-access-log",
            ],
            ROOT,
        ),
        (
            "web",
            [
                "npm",
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.web_port),
                "--strictPort",
            ],
            ROOT / "web",
        ),
        (
            "camera",
            [
                str(edge_python),
                "-m",
                "agent.vision",
                "--screen",
                args.screen,
                "--backend",
                link,
                "--camera",
                str(args.camera),
                "--markers",
                # Printed prop markers map to the running catalog's brand slugs.
                "props/markers-chile.json"
                if settings.demo_catalog == "chile"
                else "props/markers.json",
                "--device",
                "cpu",
                *agent_live_args(start_live=args.start_live),
            ],
            ROOT / "edge",
        ),
    ]
    if args.speech:
        env["ELEVENLABS_API_KEY"] = settings.elevenlabs_api_key.get_secret_value()  # type: ignore[union-attr]
        commands.append(
            (
                "speech",
                [
                    str(edge_python),
                    "-m",
                    "agent.speech",
                    "--screen",
                    args.screen,
                    "--backend",
                    link,
                    "--speech-mode",
                    "--silero-model",
                    str(args.silero_model.resolve()),
                    *agent_live_args(start_live=args.start_live),
                ],
                ROOT / "edge",
            )
        )
    log_dir = ROOT / "var/local-demo"
    log_dir.mkdir(parents=True, exist_ok=True)
    processes = [
        ManagedProcess(name, command, cwd, env, (log_dir / f"{name}.log").open("ab"))
        for name, command, cwd in commands
    ]
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(
        f"Player: {origin}/screen?id={args.screen}\nConsole: {origin}/console?screen={args.screen}",
        flush=True,
    )
    if settings.demo_key and settings.demo_key.get_secret_value():
        print(f"Demo tools (private): {origin}/demo/<DEMO_KEY from .env>", flush=True)
    if not args.start_live:
        print(
            "STANDBY: camera, mic and ad generation wait for 'Iniciar en vivo' on the console",
            flush=True,
        )
    if args.speech:
        print("Speech opted in: the mic opens only while the console is live", flush=True)
    if not args.paid_providers:
        print("Paid providers OFF: local perception and approved stock ads", flush=True)
    opened = args.no_open
    try:
        while not stopping:
            for process in processes:
                process.tick(time.monotonic())
            if not opened:
                try:
                    with urlopen(f"{origin}/health", timeout=1) as response:
                        if response.status == 200:
                            webbrowser.open(f"{origin}/screen?id={args.screen}")
                            opened = True
                except OSError:
                    pass
            time.sleep(0.5)
    finally:
        for process in reversed(processes):
            process.stop()
            process.log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
