import type { LatencyTrace } from "@/contract";

/** Nearest-rank percentile; `null` for no samples. */
export function percentile(samples: readonly number[], p: number): number | null {
    if (samples.length === 0) return null;
    const sorted = [...samples].sort((a, b) => a - b);
    const rank = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
    return sorted[rank] ?? null;
}

const ms = (iso: string | null): number | null => (iso === null ? null : Date.parse(iso));

/**
 * The intentional minimum-ad-seconds hold (cleared → swap_not_before). It is a
 * product choice, not latency, so the latency metrics exclude it.
 */
export function holdMs(trace: LatencyTrace): number {
    const cleared = ms(trace.cleared_at);
    const notBefore = ms(trace.swap_not_before);
    if (cleared === null || notBefore === null || notBefore <= cleared) return 0;
    return notBefore - cleared;
}

/** Glass-to-glass requires the player's display timestamp; the hold is excluded. */
export function glassToGlassMs(trace: LatencyTrace): number | null {
    const start = ms(trace.measured_at);
    const end = ms(trace.displayed_at);
    if (start === null || end === null || end < start) return null;
    return Math.max(0, end - start - holdMs(trace));
}

export function serverDecisionMs(trace: LatencyTrace): number | null {
    const start = ms(trace.received_at);
    const end = ms(trace.ready_at);
    if (start === null || end === null || end < start) return null;
    return Math.max(0, end - start - holdMs(trace));
}

/** The trace as ordered steps with the delta from the previous step, for the latency bar. */
export function traceSteps(trace: LatencyTrace): { step: string; atMs: number; deltaMs: number }[] {
    const order = [
        "measured_at",
        "received_at",
        "moment_changed_at",
        "cleared_at",
        "ready_at",
        "displayed_at",
    ] as const;
    const steps: { step: string; atMs: number; deltaMs: number }[] = [];
    for (const key of order) {
        const at = ms(trace[key]);
        if (at === null) continue;
        const previous = steps.at(-1);
        steps.push({
            step: key.replace(/_at$/, ""),
            atMs: at,
            deltaMs: previous ? at - previous.atMs : 0,
        });
    }
    return steps;
}

/**
 * Co-brand lift = (bundle − single-brand) / single-brand, as a display ratio.
 * Money stays decimal strings on the wire; this float is presentation only.
 */
export function coBrandLift(bundle: string, singleBrand: string): number | null {
    const single = Number(singleBrand);
    if (!Number.isFinite(single) || single <= 0) return null;
    return (Number(bundle) - single) / single;
}
