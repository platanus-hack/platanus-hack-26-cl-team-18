import type { LiveStatus } from "@/contract";
import type { Tone } from "@/shared/ui/badge";
import { generationLabel, type InputHealth } from "./input-status";

/**
 * The station bar: one state word and one row of health dots, merged from the
 * live switch (`/api/live`) and the inputs on `/health`. Each device has one
 * dot with one meaning, so the console never says "camera on" and "camera off"
 * at the same time. Pure, so every state the operator meets is unit-tested.
 */

export type StationState = "standby" | "live" | "degraded";

export interface Dot {
    readonly key: "camera" | "mic" | "screen" | "ai";
    readonly label: string;
    readonly tone: Tone;
}

export interface StationView {
    readonly state: StationState;
    readonly headline: string;
    /** The start button's label; live has no stop button (a round ends when people leave). */
    readonly toggleLabel: string;
    /** Only while live on a speech-opted-in screen: the mic does nothing otherwise. */
    readonly mic: { readonly label: string; readonly pressed: boolean } | null;
    readonly dots: readonly Dot[];
    readonly warnings: readonly string[];
}

/** "18:03": when the switch last changed, in the venue's clock. */
function since(changedAt: string): string {
    return new Date(changedAt).toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit" });
}

export function stationView(status: LiveStatus, health: InputHealth | null): StationView {
    const input = health?.inputs[status.screen_id];
    const vision = status.agents.includes("vision");
    const speech = status.agents.includes("speech");
    const player = input?.player_connected ?? false;
    const warnings: string[] = [];

    const camera: Dot = !vision
        ? { key: "camera", label: "Cámara desconectada", tone: status.live ? "danger" : "warning" }
        : !status.sensing
          ? { key: "camera", label: "Cámara lista", tone: "neutral" }
          : input?.camera_recent
            ? { key: "camera", label: "Cámara analizando", tone: "success" }
            : { key: "camera", label: "Cámara iniciando…", tone: "info" };

    const mic: Dot = !status.speech_opt_in
        ? { key: "mic", label: "Voz no habilitada", tone: "neutral" }
        : status.listening
          ? speech
              ? { key: "mic", label: "Escuchando", tone: "info" }
              : { key: "mic", label: "Micrófono sin agente", tone: "warning" }
          : {
                key: "mic",
                label: status.live && !status.mic ? "Micrófono silenciado" : "Micrófono cerrado",
                tone: "neutral",
            };

    const screen: Dot = player
        ? { key: "screen", label: "Pantalla conectada", tone: "success" }
        : { key: "screen", label: "Pantalla desconectada", tone: "danger" };

    const generation = health ? generationLabel(health) : null;
    const ai: Dot = !status.ai_generation
        ? { key: "ai", label: "IA en pausa", tone: "neutral" }
        : generation?.ai
          ? { key: "ai", label: generation.label, tone: "info" }
          : { key: "ai", label: "Copy de stock", tone: "neutral" };

    if (!player) warnings.push(`Abre /screen?id=${status.screen_id} en el TV`);

    const degraded = status.live && (!vision || !player);
    return {
        state: !status.live ? "standby" : degraded ? "degraded" : "live",
        headline: !status.live
            ? "En espera"
            : degraded
              ? "En vivo"
              : `En vivo desde ${since(status.changed_at)}`,
        toggleLabel: "Iniciar en vivo",
        mic:
            status.live && status.speech_opt_in
                ? {
                      label: status.mic ? "Silenciar micrófono" : "Activar micrófono",
                      pressed: status.mic,
                  }
                : null,
        dots: [camera, mic, screen, ai],
        warnings,
    };
}

/** One entry in the console's screen selector. */
export interface StationScreen {
    /** What the console shows ("MUT_01"). */
    readonly label: string;
    /** The real screen id behind it; null for a display-only screen that never goes live. */
    readonly id: string | null;
}

/** How many screens the selector shows. */
export const STATION_SCREEN_COUNT = 4;

/**
 * The selector's screens: MUT_01 is the one real, live-capable screen (`realId`, e.g.
 * demo-01); MUT_02… are display-only, so the console reads as a multi-screen network
 * while exactly one screen can ever be live.
 */
export function stationScreens(realId: string): readonly StationScreen[] {
    return Array.from({ length: STATION_SCREEN_COUNT }, (_, index) => ({
        label: `MUT_${String(index + 1).padStart(2, "0")}`,
        id: index === 0 ? realId : null,
    }));
}
