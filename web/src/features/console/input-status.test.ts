import { describe, expect, it } from "vitest";
import { generationLabel, InputHealthSchema, inputLabels } from "./input-status";

describe("live input status", () => {
    it("does not confuse a healthy backend with a connected live demo", () => {
        const health = InputHealthSchema.parse({ status: "ok", inputs: {} });
        expect(inputLabels(health, "demo-01")).toEqual({
            camera: "Cámara sin datos recientes",
            speech: "Voz deshabilitada",
            player: "Pantalla desconectada",
        });
    });
    it("keeps quiet speech distinct from a received utterance", () => {
        const health = InputHealthSchema.parse({
            inputs: {
                "demo-01": {
                    camera_recent: true,
                    speech_enabled: true,
                    speech_recent: false,
                    last_utterance_at: null,
                    player_connected: true,
                },
            },
        });
        expect(inputLabels(health, "demo-01").speech).toContain("esperando frase");
        health.inputs["demo-01"]!.speech_recent = true;
        expect(inputLabels(health, "demo-01").speech).toBe("Voz recibida");
        health.inputs["demo-01"]!.last_keyterm_count = 0;
        expect(inputLabels(health, "demo-01").speech).toContain("sin términos reconocidos");
        health.inputs["demo-01"]!.last_keyterm_count = 1;
        expect(inputLabels(health, "demo-01").speech).toContain("1 término reconocido");
        expect(inputLabels(health, "other").player).toBe("Pantalla desconectada");
    });
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
