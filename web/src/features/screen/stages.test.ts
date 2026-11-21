import type { CreativeStage } from "@/contract";
import { describe, expect, it } from "vitest";
import { stageAt } from "./stages";

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
