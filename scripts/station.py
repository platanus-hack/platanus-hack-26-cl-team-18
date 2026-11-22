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
from ipaddress import ip_address
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

from app.bootstrap.readiness import exposure_warnings
from app.config.settings import AppSettings

ROOT = Path(__file__).resolve().parents[1]


def lan_address() -> str | None:
    """Ask the routing table which local address reaches the venue network (no packet sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))
            host = str(probe.getsockname()[0])
        return host if not ip_address(host).is_loopback else None
    except OSError:
        return None


def public_origin(configured: str | None, *, port: int, lan_host: str | None) -> str:
    """Never print a QR that sends a phone to itself; retain an explicit tunnel origin."""
    if configured:
        host = urlsplit(configured).hostname or ""
        try:
            local = ip_address(host).is_loopback or ip_address(host).is_unspecified
        except ValueError:
            local = host == "localhost" or host.endswith(".localhost")
        if not local:
            return configured.rstrip("/")
    if lan_host:
        return f"http://{lan_host}:{port}"
    raise ValueError(
        "No phone-reachable address: connect to the venue LAN or set PUBLIC_BASE_URL to a tunnel URL"
    )


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


def web_environment(environment: dict[str, str], *, public_base_url: str) -> dict[str, str]:
    """The player/console reach the backend through their own origin (Vite proxies /api,
    /ws, /r, /media). VITE_BACKEND_URL is inlined into the page, so any value, even the
    station's 127.0.0.1, would send the second laptop's console and a judge's phone
    to their own machine (ADR 0008 D3/D7). Vite also refuses unknown Host headers, so
    the tunnel's host is allowed explicitly (IP hosts and localhost always are)."""
    result = {k: v for k, v in environment.items() if k != "VITE_BACKEND_URL"}
    result["PUBLIC_BASE_URL"] = public_base_url
    hosts = [".trycloudflare.com"]
    host = urlsplit(public_base_url).hostname or ""
    try:
        ip_address(host)
    except ValueError:
        if host and host != "localhost" and not host.endswith(".trycloudflare.com"):
            hosts.append(host)
    result["__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS"] = ",".join(hosts)
    return result


def exit_hint(log_path: Path) -> str:
    """The last line a crashed component logged (e.g. "camera 0 could not be opened")."""
    try:
        with log_path.open("rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 4096))
            lines = log.read().decode(errors="replace").splitlines()
    except OSError:
        return ""
    return next((line.strip()[:200] for line in reversed(lines) if line.strip()), "")


def agent_geometry_args(*, camera_hfov: float) -> list[str]:
    """The webcam's horizontal FOV, so the agent's near/far bands are metres on this lens
    (far = 3 m+ bills nothing). docs/stack.md specifies a >= 90 degree webcam."""
    return ["--camera-hfov", f"{camera_hfov:g}"]


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
            hint = exit_hint(Path(self.log.name)) if isinstance(self.log.name, str) else ""
            print(
                f"{self.name} exited ({code}); restarting in {min(5, self.restarts)}s"
                + (f" | last log: {hint}" if hint else ""),
                flush=True,
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
    parser.add_argument(
        "--web-host",
        default="0.0.0.0",
        help="where the player/console listen; 0.0.0.0 lets the second laptop's console "
        "in (ADR 0008 D7). The backend always stays on 127.0.0.1 behind Vite's proxy",
    )
    parser.add_argument("--screen", default="demo-01")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument(
        "--camera-hfov",
        type=float,
        default=90.0,
        help="the webcam's horizontal field of view in degrees (near/far viewer bands)",
    )
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
    # `station` is also used directly: it has the same durable prerequisites as `up`.
    from scripts.up import prepare_database, prepare_env, prepare_loops

    env_text = prepare_env()
    prepare_database(env_text)
    prepare_loops()
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
    for host, port in (("127.0.0.1", args.backend_port), (args.web_host, args.web_port)):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
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
    env["BACKEND_URL"] = backend  # Vite's proxy target (web/vite.config.ts)
    # The QR's origin is an explicit tunnel or the routed LAN player origin.
    # Never reuse a stale localhost URL: phones would connect to themselves.
    try:
        public_base_url = public_origin(
            settings.public_base_url if "public_base_url" in settings.model_fields_set else None,
            port=args.web_port,
            lan_host=lan_address(),
        )
    except ValueError as exc:
        parser.error(str(exc))
    if (
        args.web_host == "127.0.0.1"
        and urlsplit(public_base_url).scheme == "http"
        and urlsplit(public_base_url).hostname == lan_address()
    ):
        parser.error(
            "LAN QR access needs --web-host 0.0.0.0 (the default), or a tunnel PUBLIC_BASE_URL"
        )
    env = web_environment(env, public_base_url=public_base_url)
    env["SPEECH_MODE_SCREENS"] = json.dumps([args.screen] if args.speech else [])
    # The console's "Lo que ve la cámara": detection boxes, plus (demo only, never in
    # prod) a face-blurred, downscaled camera view under them (DEMO_CAMERA_PREVIEW).
    env["EDGE_DEV_STREAM"] = "1"
    env["EDGE_CAMERA_PREVIEW"] = "1" if settings.demo_camera_preview_enabled else "0"
    # Device tokens (app/api/device_auth.py): the agents read AGENT_TOKEN from the env,
    # so a token set in .env reaches them too (the backend reads .env on its own).
    if settings.agent_token and settings.agent_token.get_secret_value():
        env["AGENT_TOKEN"] = settings.agent_token.get_secret_value()
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
                args.web_host,
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
                *agent_geometry_args(camera_hfov=args.camera_hfov),
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
        f"Player: {origin}/screen?id={args.screen}&sense=0\nConsole: {origin}/console?screen={args.screen}",
        flush=True,
    )
    if args.web_host != "127.0.0.1":
        print(
            f"Second laptop: http://<this laptop's IP>:{args.web_port}/console?screen={args.screen}",
            flush=True,
        )
    print(f"QR codes open {public_base_url}/r/<code>", flush=True)
    for problem in settings.model_copy(
        update={"public_base_url": public_base_url}
    ).misconfigurations() + exposure_warnings(settings, public_base_url):
        print(f"WARNING: {problem}", flush=True)
    if settings.demo_key and settings.demo_key.get_secret_value():
        print(f"Demo tools (private): {origin}/demo/<DEMO_KEY from .env>", flush=True)
    # The player remembers the screen token after its first load (web/src/lib/device.ts);
    # it is only ever put in the URL the station opens, never printed.
    player_url = f"{origin}/screen?id={args.screen}&sense=0"  # the edge agent senses here
    if settings.screen_token and settings.screen_token.get_secret_value():
        player_url += f"&token={quote(settings.screen_token.get_secret_value())}"
        print("Player gets SCREEN_TOKEN from .env (opened for you)", flush=True)
    if settings.console_token and settings.console_token.get_secret_value():
        # Never printed: the console keeps it after one `?token=` load (web/src/lib/device.ts).
        print(
            "Console needs CONSOLE_TOKEN: open /console?token=<CONSOLE_TOKEN from .env> once",
            flush=True,
        )
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
                            webbrowser.open(player_url)
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
