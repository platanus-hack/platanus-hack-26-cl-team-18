import type { CreativeStage, CreativeStageName, DistanceBand, ScreenAttention } from "@/contract";
import { useEffect, useState } from "react";

/**
 * Dwell stages (docs/architecture.md §6.1): teaser 0–3 s → offer 3–8 s → close 8 s+.
 * While `screen.attention` arrives, the stage follows the viewers' dwell, not a
 * timer: it holds while nobody looks and never goes backwards within one
 * creative. With no attention for ATTENTION_FALLBACK_MS (an older backend or
 * the simulator) it falls back to time on air. A copy upgrade keeps the same
 * creative id, so the stage does not restart.
 */
export const STAGE_STARTS_MS: Record<CreativeStageName, number> = {
    teaser: 0,
    offer: 3_000,
    close: 8_000,
};

/** Attention older than this no longer drives the stage; timers take over. */
export const ATTENTION_FALLBACK_MS = 2_500;
const TICK_MS = 100;
/** Layout changes are coarse; 4 checks a second is plenty. */
const DISTANCE_TICK_MS = 250;
const ORDER: readonly CreativeStageName[] = ["teaser", "offer", "close"];

/** The latest `screen.attention`, stamped with the player's receive time (epoch ms). */
export interface AttentionSample {
    readonly creativeId: string | null;
    readonly looking: number;
    readonly dwellS: number;
    /** The nearest looking viewer's band, when the agent reports tracklets. */
    readonly distance: DistanceBand | null;
    readonly receivedAt: number;
}

export function toAttentionSample(message: ScreenAttention, receivedAt: number): AttentionSample {
    return {
        creativeId: message.creative_id,
        looking: message.looking,
        dwellS: message.dwell_s,
        distance: message.distance_band ?? null,
        receivedAt,
    };
}

/** Store reducer: the newest attention sample wins. */
export const latestAttention = (
    _previous: AttentionSample | null,
    sample: AttentionSample,
): AttentionSample | null => sample;

function stageForMs(ms: number): CreativeStageName {
    let name: CreativeStageName = "teaser";
    for (const candidate of ORDER) if (ms >= STAGE_STARTS_MS[candidate]) name = candidate;
    return name;
}

export interface DwellInput {
    readonly current: CreativeStageName;
    readonly playKey: string;
    /** Time since this creative mounted (ms). */
    readonly elapsedMs: number;
    /** Now, on the same clock as AttentionSample.receivedAt (epoch ms). */
    readonly now: number;
    readonly attention: AttentionSample | null;
}

/** The next stage name: pure, monotonic within one creative. */
export function nextStageName(input: DwellInput): CreativeStageName {
    const { attention } = input;
    const live =
        attention !== null &&
        input.now - attention.receivedAt <= ATTENTION_FALLBACK_MS &&
        (attention.creativeId === null || attention.creativeId === input.playKey);
    let target: CreativeStageName;
    if (!live) target = stageForMs(input.elapsedMs);
    else if (attention.looking === 0) target = input.current;
    // A look that started before this creative only counts from when it mounted.
    else target = stageForMs(Math.min(attention.dwellS * 1000, input.elapsedMs));
    return ORDER.indexOf(target) > ORDER.indexOf(input.current) ? target : input.current;
}

/** The spec's stage for a name: the latest one at or before it, else the first. */
export function stageNamed(
    name: CreativeStageName,
    stages: readonly CreativeStage[],
): CreativeStage | null {
    let current: CreativeStage | null = null;
    for (const stage of stages) {
        if (ORDER.indexOf(stage.name) <= ORDER.indexOf(name)) current = stage;
    }
    return current ?? stages[0] ?? null;
}

export function stageAt(elapsedMs: number, stages: readonly CreativeStage[]): CreativeStage | null {
    return stageNamed(stageForMs(elapsedMs), stages);
}

const noAttention = (): AttentionSample | null => null;

export function useDwellStage(
    playKey: string,
    stages: readonly CreativeStage[],
    readAttention: () => AttentionSample | null = noAttention,
): CreativeStage | null {
    // The stage is keyed by the creative: a new creative starts at teaser with no reset effect.
    const [dwell, setDwell] = useState<{ key: string; name: CreativeStageName }>({
        key: playKey,
        name: "teaser",
    });
    useEffect(() => {
        const started = Date.now();
        let name: CreativeStageName = "teaser";
        const timer = window.setInterval(() => {
            const now = Date.now();
            const next = nextStageName({
                current: name,
                playKey,
                elapsedMs: now - started,
                now,
                attention: readAttention(),
            });
            if (next === name) return;
            name = next;
            setDwell({ key: playKey, name });
        }, TICK_MS);
        return () => {
            window.clearInterval(timer);
        };
    }, [playKey, readAttention]);
    return stageNamed(dwell.key === playKey ? dwell.name : "teaser", stages);
}

/**
 * The layout's viewing distance: the live sample's band, else null (default layout).
 * Nobody looking keeps the last band so the layout doesn't jump while eyes drift.
 */
export function viewingDistance(
    sample: AttentionSample | null,
    now: number,
    previous: DistanceBand | null,
): DistanceBand | null {
    if (sample === null || now - sample.receivedAt > ATTENTION_FALLBACK_MS) return null;
    if (sample.looking === 0) return previous;
    return sample.distance ?? previous;
}

export function useViewingDistance(
    readAttention: () => AttentionSample | null = noAttention,
): DistanceBand | null {
    const [distance, setDistance] = useState<DistanceBand | null>(null);
    useEffect(() => {
        let current: DistanceBand | null = null;
        const timer = window.setInterval(() => {
            const next = viewingDistance(readAttention(), Date.now(), current);
            if (next === current) return;
            current = next;
            setDistance(next);
        }, DISTANCE_TICK_MS);
        return () => {
            window.clearInterval(timer);
        };
    }, [readAttention]);
    return distance;
}
