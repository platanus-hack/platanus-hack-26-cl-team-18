"""Glass-to-glass in a real browser: snapshot -> backend -> real web player -> pixels.

    uv run --with playwright python -m evals browser         # make eval-browser
    uv run --with playwright python -m evals browser --moments 30 --reuse-build

The production web bundle (``vite build``) is served by ``vite preview``, which
proxies /ws and /media to a fresh live eval backend, and the actual screen page
(``/screen?id=demo-01``) runs in headless Chromium at TV resolution. The eval
plays the perception agent: it sends an ``audience.snapshot`` stamped
``measured_at = now`` and watches what the page actually paints.

A probe injected before the app loads measures the page independently of the
player's own report:

* ``ws``: when ``creative.ready`` reached the browser (WebSocket wrapper);
* ``first_paint``: the first animation frame where the incoming creative is
  visibly composited (layer opacity x creative opacity > 1 %);
* ``full``: the first frame where it is fully visible (>= 99 %), i.e. after
  preload/decode and the 250 ms crossfade;
* ``blank_frames``: frames where no layer fully covers the screen (the
  "never blank" invariant, measured on pixels' compositing state).

It also reads the player's own ``player.displayed`` (through the console's
``console.latency``), so the number the console shows is checked against the
probe. All instants are epoch ms on one machine clock.

Scope: this is the screen side of glass-to-glass (snapshot sent -> pixels).
Camera -> snapshot (perception + debounce) comes from the video eval.
"""

from __future__ import annotations

import atexit
import functools
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from websockets.sync.client import ClientConnection

from app.bootstrap.demo import DEMO_SCREEN
from app.contract.messages import ConsoleLatency
from evals.live import CONSOLE_MESSAGES, live_backend
from evals.metrics import percentile
from evals.scenario import Scene, load_scenes
from evals.scorecard import RESULTS_DIR

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
OUT = RESULTS_DIR / "browser"
DIST = OUT / "dist"
SHOTS = OUT / "shots"
VIEWPORT = {"width": 1920, "height": 1080}  # the demo TV
# Alternates paid and house creatives so every moment is a real swap.
JOURNEY = ("co-brand", "fitness", "adult", "minor-safe", "coffee", "co-brand-minor", "empty")

PROBE = r"""
(() => {
  const now = () => performance.timeOrigin + performance.now();
  const T = (window.__g2g = { ws: {}, order: [], trace: {}, first: {}, full: {}, trail: {}, frames: 0, firstFrame: 0, lastFrame: 0, longFrames: 0, blank: 0, blankAt: [] });
  const Native = window.WebSocket;
  window.WebSocket = class extends Native {
    constructor(...args) {
      super(...args);
      this.addEventListener("message", (event) => {
        const at = now();
        try {
          const m = JSON.parse(event.data);
          if (m.type === "creative.ready" && !(m.creative_id in T.ws)) {
            T.ws[m.creative_id] = at;
            T.trace[m.creative_id] = m.trace;
            T.order.push(m.creative_id);
          }
        } catch (_) {}
      });
    }
  };
  const opacity = (el) => (el ? parseFloat(getComputedStyle(el).opacity || "1") : 0);
  const tick = () => {
    const at = now();
    const layers = Array.from(document.querySelectorAll("main > div"));
    if (layers.length) {
      if (T.lastFrame && at - T.lastFrame > 50) T.longFrames += 1;  // >3 frames dropped
      T.firstFrame = T.firstFrame || at;
      T.lastFrame = at;
      T.frames += 1;
      let cover = 0;
      for (const layer of layers) {
        cover = Math.max(cover, opacity(layer) * opacity(layer.firstElementChild));
      }
      if (cover < 0.99) { T.blank += 1; if (T.blankAt.length < 20) T.blankAt.push(at); }
      // Every creative the player was sent: first frame it is visibly composited,
      // and first frame it is fully visible (layer x creative opacity).
      for (const id of T.order) {
        if (id in T.full) continue;
        const layer = document.querySelector(`main > div[data-creative-id="${id}"]`);
        if (!layer) continue;
        const shown = opacity(layer) * opacity(layer.firstElementChild);
        const trail = (T.trail[id] = T.trail[id] || []);
        if (trail.length < 40) trail.push([Math.round(at - T.ws[id]), Math.round(shown * 100) / 100]);
        if (shown > 0.01 && !(id in T.first)) T.first[id] = at;
        if (shown >= 0.99) T.full[id] = at;
      }
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
})();
"""


@dataclass
class Moment:
    index: int
    scene: str
    measured_ms: float  # epoch ms stamped by the "agent" at send
    creative_id: str | None = None
    received_ms: float | None = None
    ready_ms: float | None = None
    ws_ms: float | None = None
    first_paint_ms: float | None = None
    full_ms: float | None = None
    reported_ms: float | None = None  # the player's own player.displayed
    paid: bool = False
    screenshot: str | None = None

    def since_measured(self, value: float | None) -> float | None:
        return None if value is None else value - self.measured_ms


@dataclass
class BrowserRun:
    moments: list[Moment]
    frames: int
    blank_frames: int
    duration_s: float
    fps: float | None = None
    long_frames: int = 0  # gaps > 50 ms between painted frames (jank)
    config: dict[str, Any] = field(default_factory=dict)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def build_web(*, reuse: bool) -> None:
    """Production bundle into the gitignored results dir (never touches web/dist)."""
    newest_src = max(p.stat().st_mtime for p in (WEB / "src").rglob("*") if p.is_file())
    if reuse and (DIST / "index.html").exists() and DIST.stat().st_mtime > newest_src:
        return
    subprocess.run(
        ["npx", "vite", "build", "--outDir", str(DIST), "--emptyOutDir", "--logLevel", "warn"],
        cwd=WEB,
        check=True,
    )


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    """Stop `npx` *and* the node server it spawned: the whole process group.

    Terminating only `npx` orphaned `vite preview` (dozens piled up on a dev box).
    """
    if proc.poll() is not None and not _group_alive(proc.pid):
        return
    for sig, wait_s in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            proc.poll()  # reap npx so its pid stops counting as alive
            if not _group_alive(proc.pid):
                return
            time.sleep(0.05)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def _exit_on_sigterm() -> Iterator[None]:
    """SIGTERM (CI cancel, `kill`) unwinds like Ctrl-C, so `finally` blocks still run."""

    def _raise(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    try:
        previous = signal.signal(signal.SIGTERM, _raise)
    except ValueError:  # not the main thread: leave signal handling alone
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


@contextmanager
def preview(backend_http: str) -> Iterator[str]:
    """`vite preview` of the bundle, proxying /ws, /media, /api to the eval backend."""
    port = _free_port()
    env = {**os.environ, "BACKEND_URL": backend_http}
    # stderr to a file, not a pipe nobody drains: a chatty server can't block on it.
    log = tempfile.TemporaryFile()  # noqa: SIM115 - closed in the finally below
    with _exit_on_sigterm():
        proc = subprocess.Popen(
            [
                "npx",
                "vite",
                "preview",
                "--outDir",
                str(DIST),
                "--port",
                str(port),
                "--strictPort",
                "--host",
                "127.0.0.1",
            ],
            cwd=WEB,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=log,
            start_new_session=True,  # own process group: killpg reaches node too
        )
        # Backstop for exits that skip `finally` (e.g. sys.exit from a signal elsewhere).
        cleanup = functools.partial(_kill_group, proc)
        atexit.register(cleanup)
        url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    if httpx.get(url, timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if proc.poll() is not None or time.monotonic() > deadline:
                    log.seek(0)
                    raise RuntimeError(
                        f"vite preview failed: {log.read().decode(errors='replace')}"
                    )
                time.sleep(0.2)
            yield url
        finally:
            _kill_group(proc)
            atexit.unregister(cleanup)
            log.close()


def _epoch_ms(value: str | None) -> float | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).timestamp() * 1000


def run_browser(
    *,
    moments: int = 21,
    dwell_s: float = 1.2,
    journey: Sequence[str] = JOURNEY,
    reuse_build: bool = False,
    screenshots: bool = True,
) -> BrowserRun:
    from playwright.sync_api import sync_playwright  # optional dependency: --with playwright

    scenes = load_scenes()
    build_web(reuse=reuse_build)
    if screenshots:
        shutil.rmtree(SHOTS, ignore_errors=True)
        SHOTS.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    results: list[Moment] = []
    with live_backend() as backend, preview(backend.http_url) as web:
        with sync_playwright() as pw:
            # Full Chromium with GPU rasterization: closest to the demo's Chromium kiosk.
            # The default headless shell rasterizes on the CPU and drops frames on the
            # heavier campaign creatives (measured: ~40 fps vs ~47 fps here).
            browser = pw.chromium.launch(
                channel="chromium",
                args=[
                    "--autoplay-policy=no-user-gesture-required",
                    "--enable-gpu-rasterization",
                    "--ignore-gpu-blocklist",
                ],
            )
            page = browser.new_page(viewport=VIEWPORT)
            page.add_init_script(PROBE)
            page.goto(f"{web}/screen?id={DEMO_SCREEN}")
            page.wait_for_function("window.__g2g && window.__g2g.order.length > 0", timeout=10_000)
            page.wait_for_timeout(800)  # the house ad settles
            with backend.agent() as agent, backend.console() as console:
                for index in range(moments):
                    name = journey[index % len(journey)]
                    moment = _one_moment(page, agent, console, scenes[name], index, name, dwell_s)
                    if screenshots and moment.full_ms is not None:
                        path = SHOTS / f"{index:02d}-{name}.png"
                        page.screenshot(path=str(path))
                        moment.screenshot = str(path.relative_to(OUT))
                    results.append(moment)
                frames = int(page.evaluate("window.__g2g.frames"))
                window_ms = float(page.evaluate("window.__g2g.lastFrame - window.__g2g.firstFrame"))
                long_frames = int(page.evaluate("window.__g2g.longFrames"))
                blank = int(page.evaluate("window.__g2g.blank"))
            browser.close()
    return BrowserRun(
        moments=results,
        frames=frames,
        blank_frames=blank,
        duration_s=time.perf_counter() - started,
        fps=frames / (window_ms / 1000) if window_ms else None,
        long_frames=long_frames,
        config={
            "moments": moments,
            "dwell_s": dwell_s,
            "viewport": VIEWPORT,
            "journey": list(journey),
        },
    )


def _one_moment(
    page: Any,
    agent: ClientConnection,
    console: ClientConnection,
    scene: Scene,
    index: int,
    name: str,
    dwell_s: float,
) -> Moment:
    known = int(page.evaluate("window.__g2g.order.length"))
    measured = datetime.now(UTC)
    moment = Moment(index=index, scene=name, measured_ms=measured.timestamp() * 1000)
    agent.send(
        scene.snapshot(screen_id=DEMO_SCREEN, seq=index + 1, measured_at=measured).model_dump_json()
    )
    deadline = time.monotonic() + max(dwell_s, 3.0)
    creative_id = None
    while time.monotonic() < deadline:
        state = page.evaluate(
            "(k) => { const T = window.__g2g; const id = T.order[k] || null;"
            " return id && { id, ws: T.ws[id], first: T.first[id] ?? null, full: T.full[id] ?? null,"
            " trace: T.trace[id] }; }",
            known,
        )
        if state:
            creative_id = state["id"]
            moment.creative_id = creative_id
            moment.ws_ms = state["ws"]
            moment.first_paint_ms = state["first"]
            moment.full_ms = state["full"]
            trace = state["trace"] or {}
            moment.received_ms = _epoch_ms(trace.get("received_at"))
            moment.ready_ms = _epoch_ms(trace.get("ready_at"))
            if moment.full_ms is not None:
                break
        page.wait_for_timeout(20)
    # The player's own report arrives on the console as console.latency.displayed_at.
    report_deadline = time.monotonic() + 1.0
    while creative_id and moment.reported_ms is None and time.monotonic() < report_deadline:
        try:
            message = CONSOLE_MESSAGES.validate_json(console.recv(timeout=0.1))
        except TimeoutError:
            continue
        if isinstance(message, ConsoleLatency) and message.creative_id == creative_id:
            if message.trace.displayed_at is not None:
                moment.reported_ms = message.trace.displayed_at.timestamp() * 1000
    paid = (
        page.evaluate("(id) => { const t = window.__g2g.trace[id]; return !!t; }", creative_id)
        if creative_id
        else False
    )
    moment.paid = bool(paid) and name not in ("minor-safe", "empty")
    remaining = dwell_s - (time.time() * 1000 - moment.measured_ms) / 1000
    if remaining > 0:
        page.wait_for_timeout(remaining * 1000)
    return moment


# ----------------------------------------------------------------------------- analysis

GLASS_P50_MS = 1500.0
GLASS_P95_MS = 2500.0
REPORT_AGREEMENT_MS = 50.0
HARD_CUT_MS = 100.0
# Screen-side budget (architecture §3): WS < 2 ms + decision < 30 ms + push/render
# < 100 ms + the 250 ms crossfade, with headroom.
SCREEN_SIDE_P95_MS = 500.0


def analyse(run: BrowserRun) -> dict[str, Any]:
    ms = run.moments

    def series(fn: Any) -> list[float]:
        return [v for m in ms if (v := fn(m)) is not None]

    stages = {
        "agent → backend received": series(lambda m: m.since_measured(m.received_ms)),
        "backend decision (received → ready)": series(
            lambda m: (
                None if m.ready_ms is None or m.received_ms is None else m.ready_ms - m.received_ms
            )
        ),
        "ready → creative.ready in the browser": series(
            lambda m: None if m.ws_ms is None or m.ready_ms is None else m.ws_ms - m.ready_ms
        ),
        "browser: arrival → first painted frame (preload, decode, React)": series(
            lambda m: (
                None if m.first_paint_ms is None or m.ws_ms is None else m.first_paint_ms - m.ws_ms
            )
        ),
        "browser: first frame → fully visible (crossfade)": series(
            lambda m: (
                None
                if m.full_ms is None or m.first_paint_ms is None
                else m.full_ms - m.first_paint_ms
            )
        ),
    }
    first = series(lambda m: m.since_measured(m.first_paint_ms))
    full = series(lambda m: m.since_measured(m.full_ms))
    reported = series(lambda m: m.since_measured(m.reported_ms))
    agreement = series(
        lambda m: (
            None if m.reported_ms is None or m.full_ms is None else abs(m.reported_ms - m.full_ms)
        )
    )
    table = {
        name: {"p50": percentile(v, 50), "p95": percentile(v, 95), "n": len(v)}
        for name, v in stages.items()
    }
    split = {name: s["p50"] or 0.0 for name, s in table.items()}
    bottleneck = max(split, key=lambda k: split[k]) if split else None
    metrics = {
        "browser_moments": len(ms),
        "browser_swaps_seen": sum(m.full_ms is not None for m in ms),
        "browser_glass_first_paint_p50_ms": percentile(first, 50),
        "browser_glass_p50_ms": percentile(full, 50),
        "browser_glass_p95_ms": percentile(full, 95),
        "browser_glass_max_ms": max(full, default=None),
        "browser_player_reported_p50_ms": percentile(reported, 50),
        "browser_report_vs_probe_p95_ms": percentile(agreement, 95),
        "browser_reports_received": len(reported),
        "browser_missed_swaps": sum(m.full_ms is None for m in ms),
        # A swap that went from invisible to fully visible in under 100 ms skipped
        # the 250 ms crossfade: a hard cut (the race fixed with a keyframe animation).
        "browser_hard_cuts": sum(
            1
            for m in ms
            if m.full_ms is not None
            and m.first_paint_ms is not None
            and m.full_ms - m.first_paint_ms < HARD_CUT_MS
        ),
        "browser_blank_frames": run.blank_frames,
        "browser_frames": run.frames,
        "browser_fps": run.fps,
        "browser_long_frames": run.long_frames,
    }
    return {"stages": table, "bottleneck": bottleneck, "metrics": metrics}


def render(run: BrowserRun, analysis: dict[str, Any]) -> str:
    def f(v: Any) -> str:
        return "—" if v is None else f"{v:.0f}"

    total = sum((s["p50"] or 0) for s in analysis["stages"].values()) or 1
    lines = [
        "## Glass-to-glass in Chromium (screen side: snapshot sent → pixels)",
        "",
        f"{run.config['moments']} moments · dwell {run.config['dwell_s']} s · "
        f"{run.config['viewport']['width']}×{run.config['viewport']['height']} · "
        f"{run.frames} frames sampled ({run.fps or 0:.0f} fps, {run.long_frames} gaps > 50 ms) · "
        f"{run.blank_frames} frames without full coverage",
        "",
        "| Stage | p50 | p95 | n | Share of p50 |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, s in analysis["stages"].items():
        lines.append(
            f"| {name} | {f(s['p50'])} | {f(s['p95'])} | {s['n']} | {(s['p50'] or 0) / total:.0%} |"
        )
    m = analysis["metrics"]
    lines += [
        "",
        f"**Snapshot → fully visible:** p50 {f(m['browser_glass_p50_ms'])} ms, p95 {f(m['browser_glass_p95_ms'])} ms "
        f"(first pixels p50 {f(m['browser_glass_first_paint_p50_ms'])} ms). "
        f"**Bottleneck: {analysis['bottleneck']}.** Player-reported displayed_at vs probe: p95 "
        f"{f(m['browser_report_vs_probe_p95_ms'])} ms apart ({m['browser_reports_received']} reports).",
        "",
        "| # | Scene | Creative on screen at (ms after snapshot): ws | first paint | full | player says |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for mo in run.moments:
        lines.append(
            f"| {mo.index} | {mo.scene} | {f(mo.since_measured(mo.ws_ms))} | {f(mo.since_measured(mo.first_paint_ms))} | "
            f"{f(mo.since_measured(mo.full_ms))} | {f(mo.since_measured(mo.reported_ms))} |"
        )
    return "\n".join(lines) + "\n"


def to_json(run: BrowserRun) -> str:
    return json.dumps([m.__dict__ for m in run.moments], indent=1)
