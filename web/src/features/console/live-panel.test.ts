import type { LiveStatus } from "@/contract";
import { describe, expect, it } from "vitest";
import { livePanelView } from "./live-panel";

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

const chip = (view: ReturnType<typeof livePanelView>, key: string) =>
    view.chips.find((c) => c.key === key)!;

describe("livePanelView", () => {
    it("standby offers to go live and shows everything paused", () => {
        const view = livePanelView(status());
        expect(view.headline).toBe("En espera");
        expect(view.toggleLabel).toBe("Iniciar en vivo");
        expect(view.toggleTo).toBe(true);
        expect(chip(view, "camera").label).toBe("Cámara en espera");
        expect(chip(view, "mic").label).toBe("Micrófono cerrado");
        expect(chip(view, "ai").label).toBe("Generación de anuncios en pausa");
        expect(view.warnings).toEqual([]);
    });

    it("live with both agents senses, listens and generates", () => {
        const view = livePanelView(status({ live: true }));
        expect(view.headline).toBe("En vivo");
        expect(view.toggleTo).toBe(false);
        expect(chip(view, "camera")).toMatchObject({ label: "Cámara analizando", tone: "success" });
        expect(chip(view, "mic")).toMatchObject({ label: "Micrófono escuchando", tone: "info" });
        expect(chip(view, "ai").tone).toBe("success");
        expect(view.mic).toEqual({ label: "Silenciar micrófono", pressed: true });
    });

    it("a muted mic while live says so and offers to unmute", () => {
        const view = livePanelView(status({ live: true, mic: false }));
        expect(chip(view, "mic").label).toBe("Micrófono silenciado");
        expect(view.mic).toEqual({ label: "Activar micrófono", pressed: false });
    });

    it("warns when live without a camera agent", () => {
        const view = livePanelView(status({ live: true, agents: ["speech"] }));
        expect(chip(view, "camera")).toMatchObject({
            label: "Cámara desconectada",
            tone: "danger",
        });
        expect(view.warnings[0]).toContain("agente de cámara");
    });

    it("warns when the mic is on but no speech agent listens", () => {
        const view = livePanelView(status({ live: true, agents: ["vision"] }));
        expect(chip(view, "mic").tone).toBe("warning");
        expect(view.warnings).toHaveLength(1);
    });

    it("a screen without speech opt-in has no mic control", () => {
        const view = livePanelView(status({ live: true, speech_opt_in: false }));
        expect(view.mic).toBeNull();
        expect(chip(view, "mic").label).toBe("Voz no habilitada en esta pantalla");
        expect(view.warnings).toEqual([]);
    });
});
