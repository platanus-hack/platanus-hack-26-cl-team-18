import type { DistanceBand } from "@/contract";
import type { Tone } from "@/shared/ui/badge";
import { useEffect, useState } from "react";

/** A re-render clock for ages and TTLs; render code never reads the time itself. */
export function useNow(intervalMs = 1000): number {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        const timer = setInterval(() => {
            setNow(Date.now());
        }, intervalMs);
        return () => {
            clearInterval(timer);
        };
    }, [intervalMs]);
    return now;
}

export const DISTANCE: Record<DistanceBand, string> = {
    near: "cerca",
    mid: "media",
    far: "lejos",
};

export const pct = (ratio: number) => `${Math.round(ratio * 100)} %`;

/** "15:04:05.123", local time. */
export function clock(input: string | number): string {
    const date = new Date(input);
    const hms = date.toLocaleTimeString("es-CL", { hour12: false });
    return `${hms}.${String(date.getMilliseconds()).padStart(3, "0")}`;
}

/** "3 s", "1,2 s", "ya" for elapsed or remaining seconds. */
export function seconds(ms: number): string {
    if (ms <= 0) return "0 s";
    const s = ms / 1000;
    return s < 10 ? `${s.toFixed(1).replace(".", ",")} s` : `${Math.round(s)} s`;
}

export const OUTCOME_TONE: Record<string, Tone> = {
    ok: "success",
    accepted: "success",
    done: "success",
    request: "success",
    failed: "danger",
    timeout: "danger",
    rejected: "warning",
    stale: "warning",
    lower_score: "neutral",
    budget_denied: "warning",
    disabled: "neutral",
    running: "info",
    queued: "neutral",
};

export const toneOf = (outcome: string | null | undefined): Tone =>
    (outcome && OUTCOME_TONE[outcome]) || "neutral";

/** Provider colour on the timeline (always paired with a text label). */
export const PROVIDER_FILL: Record<string, string> = {
    claude: "bg-accent",
    vlm: "bg-info",
    elevenlabs: "bg-success",
    fal: "bg-warning",
};

export const providerFill = (provider: string | null) =>
    (provider && PROVIDER_FILL[provider]) || "bg-muted";
