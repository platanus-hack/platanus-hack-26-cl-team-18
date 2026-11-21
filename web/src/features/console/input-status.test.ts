import { describe, expect, it } from "vitest";
import { InputHealthSchema, inputLabels } from "./input-status";

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
        expect(inputLabels(health, "other").player).toBe("Pantalla desconectada");
    });
});
