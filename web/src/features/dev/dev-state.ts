import type {
    ConsoleAuction,
    ConsoleOutbound,
    CreativeReady,
    CreativeUpgraded,
    DevCreative,
    DevJob,
    DevKeyframe,
    DevOutbound,
    DevPerception,
    DevPlayer,
    DevProviderLoad,
    DevSpeech,
    DevVlm,
    LatencyTrace,
} from "@/contract";
import { consoleReducer, initialConsoleState, type ConsoleState } from "@/features/console";
import type { ConnectionState } from "@/lib/ws/typed-socket";

/**
 * Everything /dev shows, reduced from the dev socket (`/ws/dev`). The console half
 * reuses the console reducer; the dev half keeps bounded ring buffers, newest first.
 * Pure: the receive time is passed in, never read from a clock here.
 */

export const WINDOWS = {
    perception: 50,
    vlm: 20,
    speech: 20,
    creatives: 50,
    player: 50,
    auctions: 20,
    traces: 12,
    jobs: 200,
    log: 500,
} as const;

/** A keyframe older than this is not drawn: the schematic is the truth by default. */
export const KEYFRAME_TTL_MS = 5_000;

export interface PerceptionSample {
    readonly atMs: number;
    readonly inferenceMs: number;
    readonly roundTripMs: number | null;
    readonly people: number;
}

export type OnAir = CreativeReady | CreativeUpgraded;

export interface TraceEntry {
    readonly creativeId: string;
    readonly trace: LatencyTrace;
}

export interface DevScreen {
    readonly perception: DevPerception | null;
    readonly perceptionHistory: readonly PerceptionSample[];
    readonly vlm: readonly DevVlm[];
    readonly speech: readonly DevSpeech[];
    readonly creatives: readonly DevCreative[];
    readonly player: readonly DevPlayer[];
    readonly auctions: readonly ConsoleAuction[];
    /** The mirror: what the screen should be showing (never a second player). */
    readonly onAir: OnAir | null;
    /** Newest first, one per creative; the timeline's hot rows. */
    readonly traces: readonly TraceEntry[];
    readonly keyframe: { readonly frame: DevKeyframe; readonly receivedAtMs: number } | null;
}

export interface LogEntry {
    readonly id: number;
    readonly receivedAtMs: number;
    readonly type: DevOutbound["type"];
    readonly screenId: string | null;
    readonly message: DevOutbound;
}

export interface DevState {
    readonly console: ConsoleState;
    readonly screens: Readonly<Record<string, DevScreen>>;
    /** Warm jobs across screens, newest first. */
    readonly jobs: readonly DevJob[];
    readonly log: readonly LogEntry[];
    readonly logSeq: number;
    readonly paused: boolean;
    /** The backend closed /ws/dev with 4404: dev views are off in this environment. */
    readonly disabled: boolean;
}

export type DevEvent =
    | { readonly type: "message"; readonly message: DevOutbound; readonly receivedAtMs: number }
    | { readonly type: "connection"; readonly state: ConnectionState }
    | { readonly type: "pause"; readonly paused: boolean }
    | { readonly type: "disabled" };

export const emptyDevScreen: DevScreen = {
    perception: null,
    perceptionHistory: [],
    vlm: [],
    speech: [],
    creatives: [],
    player: [],
    auctions: [],
    onAir: null,
    traces: [],
    keyframe: null,
};

export const initialDevState: DevState = {
    console: initialConsoleState,
    screens: {},
    jobs: [],
    log: [],
    logSeq: 0,
    paused: false,
    disabled: false,
};

const push = <T>(items: readonly T[], item: T, limit: number): readonly T[] =>
    [item, ...items].slice(0, limit);

function isConsoleMessage(m: DevOutbound): m is ConsoleOutbound {
    return !m.type.startsWith("dev.");
}

function screenOf(m: DevOutbound): string | null {
    return "screen_id" in m ? m.screen_id : null;
}

/** The keyframe bytes never enter the log (or an export): only their size does. */
function forLog(m: DevOutbound): DevOutbound {
    return m.type === "dev.keyframe"
        ? { ...m, jpeg_base64: `(${m.jpeg_base64.length} base64 chars omitted)` }
        : m;
}

function withTrace(traces: readonly TraceEntry[], entry: TraceEntry): readonly TraceEntry[] {
    const rest = traces.filter((t) => t.creativeId !== entry.creativeId);
    const existing = traces.find((t) => t.creativeId === entry.creativeId);
    // Keep a known displayed_at: an upgrade's trace may arrive before the latency report.
    const trace =
        existing && entry.trace.displayed_at === null && existing.trace.displayed_at !== null
            ? { ...entry.trace, displayed_at: existing.trace.displayed_at }
            : entry.trace;
    return [{ creativeId: entry.creativeId, trace }, ...rest].slice(0, WINDOWS.traces);
}

/** Latest wins; an upgrade applies only to the creative it upgrades (like the player). */
export function nextOnAir(current: OnAir | null, m: OnAir): OnAir | null {
    if (m.type === "creative.ready") return m;
    if (current === null || current.creative_id === m.creative_id) return m;
    return current;
}

function devScreen(view: DevScreen, m: DevOutbound, receivedAtMs: number): DevScreen {
    switch (m.type) {
        case "dev.perception":
            return {
                ...view,
                perception: m,
                perceptionHistory: push(
                    view.perceptionHistory,
                    {
                        atMs: Date.parse(m.measured_at),
                        inferenceMs: m.inference_ms,
                        roundTripMs: m.round_trip_ms,
                        people: m.people.length,
                    },
                    WINDOWS.perception,
                ),
            };
        case "dev.vlm":
            return { ...view, vlm: push(view.vlm, m, WINDOWS.vlm) };
        case "dev.speech":
            return { ...view, speech: push(view.speech, m, WINDOWS.speech) };
        case "dev.creative":
            return { ...view, creatives: push(view.creatives, m, WINDOWS.creatives) };
        case "dev.player":
            return { ...view, player: push(view.player, m, WINDOWS.player) };
        case "dev.keyframe":
            return { ...view, keyframe: { frame: m, receivedAtMs } };
        case "console.auction":
            return { ...view, auctions: push(view.auctions, m, WINDOWS.auctions) };
        case "creative.ready":
        case "creative.upgraded":
            return {
                ...view,
                onAir: nextOnAir(view.onAir, m),
                traces: withTrace(view.traces, { creativeId: m.creative_id, trace: m.trace }),
            };
        case "console.latency":
            return {
                ...view,
                traces: withTrace(view.traces, { creativeId: m.creative_id, trace: m.trace }),
            };
        default:
            return view;
    }
}

export function devReducer(state: DevState, event: DevEvent): DevState {
    switch (event.type) {
        case "connection":
            return { ...state, console: consoleReducer(state.console, event) };
        case "pause":
            return { ...state, paused: event.paused };
        case "disabled":
            return { ...state, disabled: true };
        case "message":
            break;
    }
    const m = event.message;
    const consoleState = isConsoleMessage(m)
        ? consoleReducer(state.console, { type: "message", message: m })
        : state.console;
    const screenId = screenOf(m);
    const screens =
        screenId === null
            ? state.screens
            : {
                  ...state.screens,
                  [screenId]: devScreen(
                      state.screens[screenId] ?? emptyDevScreen,
                      m,
                      event.receivedAtMs,
                  ),
              };
    const jobs = m.type === "dev.job" ? push(state.jobs, m, WINDOWS.jobs) : state.jobs;
    const log = state.paused
        ? state.log
        : push(
              state.log,
              {
                  id: state.logSeq,
                  receivedAtMs: event.receivedAtMs,
                  type: m.type,
                  screenId,
                  message: forLog(m),
              },
              WINDOWS.log,
          );
    return {
        ...state,
        console: consoleState,
        screens,
        jobs,
        log,
        logSeq: state.paused ? state.logSeq : state.logSeq + 1,
    };
}

// ------------------------------------------------------------------ derived views

/** In-flight warm jobs per provider, as reported by the newest job event. */
export function providerLoad(jobs: readonly DevJob[]): readonly DevProviderLoad[] {
    return jobs[0]?.load ?? [];
}

export interface JobSpan {
    readonly id: string;
    readonly provider: string;
    readonly kind: DevJob["kind"];
    readonly key: string;
    readonly screenId: string | null;
    /** Submitted (queued) instant; `startMs` is when it started running. */
    readonly queuedMs: number;
    readonly startMs: number;
    readonly endMs: number | null;
    readonly state: "queued" | "running" | "done" | "failed";
}

/**
 * Pair job events into spans: submitted → running → done/failed, per provider+key.
 * Deduped and rejected submissions never ran, so they have no span.
 */
export function jobSpans(jobs: readonly DevJob[]): readonly JobSpan[] {
    const spans: JobSpan[] = [];
    const open = new Map<string, number>(); // provider:key -> index in spans
    for (const job of [...jobs].reverse()) {
        const id = `${job.provider}:${job.key}`;
        const at = Date.parse(job.at);
        const index = open.get(id);
        const current = index === undefined ? undefined : spans[index];
        switch (job.state) {
            case "submitted":
                open.set(id, spans.length);
                spans.push({
                    id: `${id}#${spans.length}`,
                    provider: job.provider,
                    kind: job.kind,
                    key: job.key,
                    screenId: job.screen_id,
                    queuedMs: at,
                    startMs: at,
                    endMs: null,
                    state: "queued",
                });
                break;
            case "running":
                if (current && index !== undefined) {
                    spans[index] = { ...current, startMs: at, state: "running" };
                } else {
                    open.set(id, spans.length);
                    spans.push({
                        id: `${id}#${spans.length}`,
                        provider: job.provider,
                        kind: job.kind,
                        key: job.key,
                        screenId: job.screen_id,
                        queuedMs: at,
                        startMs: at,
                        endMs: null,
                        state: "running",
                    });
                }
                break;
            case "done":
            case "failed": {
                const state = job.state;
                if (current && index !== undefined) {
                    spans[index] = { ...current, endMs: at, state };
                } else {
                    const start = at - (job.elapsed_ms ?? 0);
                    spans.push({
                        id: `${id}#${spans.length}`,
                        provider: job.provider,
                        kind: job.kind,
                        key: job.key,
                        screenId: job.screen_id,
                        queuedMs: start,
                        startMs: start,
                        endMs: at,
                        state,
                    });
                }
                open.delete(id);
                break;
            }
            case "deduped":
            case "rejected":
                break;
        }
    }
    return spans.reverse();
}

const HOT_STEPS = [
    ["measured_at", "received_at", "red"],
    ["received_at", "cleared_at", "decisión"],
    ["cleared_at", "ready_at", "creatividad"],
    ["ready_at", "displayed_at", "pantalla"],
] as const;

export interface TimelineSegment {
    readonly fromMs: number;
    readonly toMs: number;
    readonly label: string;
}

export interface TimelineRow {
    readonly id: string;
    readonly label: string;
    readonly lane: "hot" | "warm";
    readonly provider: string | null;
    readonly state: JobSpan["state"] | "hot";
    readonly segments: readonly TimelineSegment[];
}

export interface Timeline {
    readonly startMs: number;
    readonly endMs: number;
    readonly rows: readonly TimelineRow[];
}

const at = (iso: string | null): number | null => (iso === null ? null : Date.parse(iso));

/**
 * One time axis for the hot path (latency trace per creative) and the warm path
 * (job spans), over the last `windowMs` of activity. Open spans run to `nowMs`.
 */
export function buildTimeline(
    traces: readonly TraceEntry[],
    spans: readonly JobSpan[],
    { windowMs, nowMs }: { windowMs: number; nowMs: number },
): Timeline | null {
    const rows: TimelineRow[] = [];
    for (const { creativeId, trace } of traces) {
        const segments: TimelineSegment[] = [];
        for (const [from, to, label] of HOT_STEPS) {
            const a = at(trace[from]);
            const b = at(trace[to]);
            if (a !== null && b !== null && b >= a) segments.push({ fromMs: a, toMs: b, label });
        }
        const upgraded = at(trace.upgraded_at);
        const ready = at(trace.ready_at);
        if (upgraded !== null && ready !== null && upgraded > ready) {
            segments.push({ fromMs: upgraded, toMs: upgraded, label: "mejora" });
        }
        if (segments.length > 0) {
            rows.push({
                id: `hot:${creativeId}`,
                label: `creatividad ${creativeId.slice(-6)}`,
                lane: "hot",
                provider: null,
                state: "hot",
                segments,
            });
        }
    }
    for (const span of spans) {
        const end = span.endMs ?? nowMs;
        const segments: TimelineSegment[] = [];
        if (span.startMs > span.queuedMs) {
            segments.push({ fromMs: span.queuedMs, toMs: span.startMs, label: "en cola" });
        }
        segments.push({ fromMs: span.startMs, toMs: end, label: span.state });
        rows.push({
            id: `warm:${span.id}`,
            label: `${span.provider} · ${span.kind}`,
            lane: "warm",
            provider: span.provider,
            state: span.state,
            segments,
        });
    }
    const ends = rows.flatMap((r) => r.segments.map((s) => s.toMs));
    if (ends.length === 0) return null;
    const endMs = Math.max(...ends);
    const startMs = endMs - windowMs;
    const visible = rows.filter((r) => r.segments.some((s) => s.toMs >= startMs));
    if (visible.length === 0) return null;
    return { startMs, endMs, rows: visible };
}

/** The build steps that belong to a creative, oldest first (plus its brief's AI attempts). */
export function buildTrace(
    creatives: readonly DevCreative[],
    creativeId: string,
): readonly DevCreative[] {
    const own = creatives.filter((c) => c.creative_id === creativeId);
    const signatures = new Set(own.flatMap((c) => (c.brief ? [c.brief.signature] : [])));
    return creatives
        .filter(
            (c) =>
                c.creative_id === creativeId ||
                (c.creative_id === null && c.brief !== null && signatures.has(c.brief.signature)),
        )
        .slice()
        .reverse();
}
