import { describe, expect, it } from "vitest";
import { generationLabel, InputHealthSchema } from "./input-status";

describe("live input status", () => {
    it("says plainly when ads fall back to stock copy for lack of keys", () => {
        const none = InputHealthSchema.parse({ inputs: {} });
        expect(generationLabel(none)).toEqual({
            label: "Sin claves de IA: anuncios con copy aprobado de stock",
            ai: false,
        });
        const keyed = InputHealthSchema.parse({
            inputs: {},
            ads: { providers: { ai_copy: { configured: true }, voice: { configured: true } } },
        });
        expect(generationLabel(keyed).ai).toBe(true);
    });
});
