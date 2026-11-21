import type { CreativeStage } from "@/contract";
import { describe, expect, it } from "vitest";
import {
    ATTENTION_FALLBACK_MS,
    nextStageName,
    stageAt,
    stageNamed,
    viewingDistance,
    type AttentionSample,
} from "./stages";

const STAGES: CreativeStage[] = [
    { name: "teaser", copy_text: "¿Con hambre los 2?" },
    { name: "offer", copy_text: "Para los 2 · Dúo por $5.990" },
    { name: "close", copy_text: "Escanea el QR · válido 10 min" },
];

describe("stageAt", () => {
    it("walks teaser → offer → close on time", () => {
        expect(stageAt(0, STAGES)?.name).toBe("teaser");
        expect(stageAt(2_999, STAGES)?.name).toBe("teaser");
        expect(stageAt(3_000, STAGES)?.name).toBe("offer");
        expect(stageAt(8_000, STAGES)?.name).toBe("close");
        expect(stageAt(60_000, STAGES)?.name).toBe("close");
    });

    it("falls back to the first stage, or nothing for a legacy spec", () => {
        expect(stageAt(9_000, [STAGES[1]!])?.name).toBe("offer");
        expect(stageAt(0, [])).toBeNull();
    });
});

describe("nextStageName (attention-driven dwell)", () => {
    const look = (
        dwellS: number,
        looking = 1,
        receivedAt = 10_000,
        creativeId: string | null = "c1",
    ) => ({ creativeId, looking, dwellS, distance: null, receivedAt }) satisfies AttentionSample;
    const base = { current: "teaser", playKey: "c1", elapsedMs: 20_000, now: 10_000 } as const;

    it.each([
        ["teaser while dwell < 3 s", base, look(2.9), "teaser"],
        ["offer at 3 s", base, look(3), "offer"],
        ["offer up to 8 s", base, look(7.9), "offer"],
        ["close at 8 s", base, look(8), "close"],
        ["attention drops → hold", { ...base, current: "offer" }, look(0, 0), "offer"],
        ["nobody looking holds teaser despite time on air", base, look(0, 0), "teaser"],
        ["never regress when dwell falls", { ...base, current: "close" }, look(1), "close"],
        [
            "a look older than the creative counts from its mount",
            { ...base, elapsedMs: 1_000 },
            look(12),
            "teaser",
        ],
        ["fallback to timers with no attention", { ...base, elapsedMs: 3_000 }, null, "offer"],
        [
            "fallback to timers when attention is stale (> 2.5 s)",
            { ...base, elapsedMs: 8_000, now: 10_000 + ATTENTION_FALLBACK_MS + 1 },
            look(0, 0),
            "close",
        ],
        [
            "attention for another creative is ignored",
            { ...base, elapsedMs: 500 },
            look(9, 1, 10_000, "other"),
            "teaser",
        ],
        ["a null creative id still drives the stage", base, look(9, 1, 10_000, null), "close"],
    ] as const)("%s", (_label, input, attention, expected) => {
        expect(nextStageName({ ...input, attention })).toBe(expected);
    });

    it("maps a name to the spec's stage", () => {
        expect(stageNamed("close", STAGES)?.copy_text).toContain("QR");
        expect(stageNamed("teaser", [STAGES[1]!])?.name).toBe("offer");
    });
});

describe("viewingDistance (distance-aware layout)", () => {
    const sample = (
        distance: AttentionSample["distance"],
        looking = 1,
        receivedAt = 10_000,
    ): AttentionSample => ({ creativeId: "c1", looking, dwellS: 1, distance, receivedAt });

    it.each([
        ["follows the nearest looking viewer", sample("far"), null, "far"],
        ["a viewer stepping close switches to near", sample("near"), "far", "near"],
        ["nobody looking keeps the last band", sample("near", 0), "far", "far"],
        ["an unknown band keeps the last one", sample(null), "mid", "mid"],
        ["no attention: default layout", null, "near", null],
    ] as const)("%s", (_label, input, previous, expected) => {
        expect(viewingDistance(input, 10_000, previous)).toBe(expected);
    });

    it("stale attention drops back to the default layout", () => {
        expect(
            viewingDistance(sample("far"), 10_000 + ATTENTION_FALLBACK_MS + 1, "far"),
        ).toBeNull();
    });
});
