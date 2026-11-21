import type { CreativeStage, CreativeStageName } from "@/contract";
import { useEffect, useState } from "react";

/**
 * Dwell stages (docs/architecture.md §6.1): teaser 0–3 s → offer 3–8 s → close 8 s+.
 * Until the agent's dwell signal drives the player, the stage follows time on air.
 * A copy upgrade keeps the same creative id, so the stage does not restart.
 */
export const STAGE_STARTS_MS: Record<CreativeStageName, number> = {
    teaser: 0,
    offer: 3_000,
    close: 8_000,
};

export function stageAt(elapsedMs: number, stages: readonly CreativeStage[]): CreativeStage | null {
    let current: CreativeStage | null = null;
    for (const stage of stages) {
        if (elapsedMs >= STAGE_STARTS_MS[stage.name]) current = stage;
    }
    return current ?? stages[0] ?? null;
}

export function useDwellStage(
    playKey: string,
    stages: readonly CreativeStage[],
): CreativeStage | null {
    // Elapsed time is keyed by the creative: a new creative starts at 0 with no reset effect.
    const [tick, setTick] = useState({ key: playKey, elapsed: 0 });
    useEffect(() => {
        const started = performance.now();
        const timers = Object.values(STAGE_STARTS_MS)
            .filter((at) => at > 0)
            .map((at) =>
                window.setTimeout(() => {
                    setTick({ key: playKey, elapsed: performance.now() - started });
                }, at),
            );
        return () => {
            timers.forEach((t) => {
                window.clearTimeout(t);
            });
        };
    }, [playKey]);
    return stageAt(tick.key === playKey ? tick.elapsed : 0, stages);
}
