import type { LiveStatus } from "@/contract";
import { describe, expect, it } from "vitest";
import type { InputHealth } from "./input-status";
import { stationScreens, stationView } from "./station";

function status(overrides: Partial<LiveStatus> = {}): LiveStatus {
    const live = overrides.live ?? false;
    const mic = overrides.mic ?? true;
    const optIn = overrides.speech_opt_in ?? true;
    return {
        type: "live.status",
        v: 1,
        screen_id: "demo-01",
        revision: 0,
        live,
        mic,
        speech_opt_in: optIn,
        sensing: live,
        listening: live && mic && optIn,
        ai_generation: live,
        agents: ["speech", "vision"],
        changed_at: "2026-11-21T15:00:00Z",
        ...overrides,
    };
}

function health(input: Partial<InputHealth["inputs"][string]> = {}, ai = false): InputHealth {
    return {
        ads: { providers: { ai_copy: { configured: ai }, voice: { configured: ai } } },
        inputs: {
            "demo-01": {
                camera_recent: false,
                speech_enabled: true,
                speech_recent: false,
                last_utterance_at: null,
                last_keyterm_count: null,
                player_connected: true,
                ...input,
            },
        },
    };
}

const dot = (view: ReturnType<typeof stationView>, key: string) =>
    view.dots.find((d) => d.key === key)!;

describe("stationView", () => {
    it("standby never reads as broken: one calm word, ready devices, no mic control", () => {
        const view = stationView(status(), health());
        expect(view.state).toBe("standby");
        expect(view.headline).toBe("En espera");
        expect(view.toggleLabel).toBe("Iniciar en vivo");
        expect(dot(view, "camera")).toMatchObject({ label: "Cámara lista", tone: "neutral" });
        expect(dot(view, "mic").label).toBe("Micrófono cerrado");
        expect(dot(view, "ai").label).toBe("IA en pausa");
        expect(view.mic).toBeNull();
        expect(view.warnings).toEqual([]);
    });

    it("live with everything connected senses, listens and offers to mute", () => {
        const view = stationView(status({ live: true }), health({ camera_recent: true }, true));
        expect(view.state).toBe("live");
        expect(dot(view, "camera")).toMatchObject({ label: "Cámara analizando", tone: "success" });
        expect(dot(view, "mic").label).toBe("Escuchando");
        expect(dot(view, "ai").tone).toBe("info");
        expect(view.mic).toEqual({ label: "Silenciar micrófono", pressed: true });
    });

    it("says the camera is starting, not broken, in the second after going live", () => {
        const view = stationView(status({ live: true }), health({ camera_recent: false }));
        expect(dot(view, "camera").label).toBe("Cámara iniciando…");
        expect(view.state).toBe("live");
    });

    it("is degraded when live without a camera agent or a screen", () => {
        const noCamera = stationView(status({ live: true, agents: ["speech"] }), health());
        expect(noCamera.state).toBe("degraded");
        expect(dot(noCamera, "camera").tone).toBe("danger");
        const noScreen = stationView(status({ live: true }), health({ player_connected: false }));
        expect(noScreen.state).toBe("degraded");
        expect(noScreen.warnings[0]).toContain("/screen?id=demo-01");
    });

    it("live without AI keys says ads use approved stock copy", () => {
        const view = stationView(status({ live: true }), health({ camera_recent: true }));
        expect(dot(view, "ai").label).toContain("stock");
    });

    it("a screen without speech opt-in has no mic control", () => {
        const view = stationView(status({ live: true, speech_opt_in: false }), health());
        expect(view.mic).toBeNull();
        expect(dot(view, "mic").label).toBe("Voz no habilitada");
    });
});

describe("stationScreens", () => {
    it("shows MUT_01… but only MUT_01 is a real, live-capable screen", () => {
        const screens = stationScreens("demo-01");
        expect(screens.map((s) => s.label)).toEqual(["MUT_01", "MUT_02", "MUT_03", "MUT_04"]);
        expect(screens.filter((s) => s.id !== null)).toEqual([{ label: "MUT_01", id: "demo-01" }]);
    });
});
