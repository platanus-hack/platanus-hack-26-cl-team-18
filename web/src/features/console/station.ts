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
    readonly detail: string;
    readonly toggleLabel: string;
    readonly toggleTo: boolean;
    /** Only while live on a speech-opted-in screen: the mic does nothing otherwise. */
    readonly mic: { readonly label: string; readonly pressed: boolean } | null;
    readonly dots: readonly Dot[];
    readonly warnings: readonly string[];
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
          : { key: "ai", label: "Copy aprobado de stock (sin claves de IA)", tone: "neutral" };

    if (!player) warnings.push("Abre la pantalla en el TV: /screen?id=" + status.screen_id);
    if (status.live && !vision)
        warnings.push("No hay agente de cámara conectado: nadie será detectado.");
    if (status.listening && !speech)
        warnings.push("El micrófono está activado, pero el agente de voz no está conectado.");

    const degraded = status.live && (!vision || !player);
    return {
        state: !status.live ? "standby" : degraded ? "degraded" : "live",
        headline: !status.live ? "En espera" : degraded ? "En vivo · con problemas" : "En vivo",
        detail: status.live
            ? "La pantalla ve, escucha y subasta en tiempo real."
            : "Nada se analiza ni se genera. La pantalla muestra el anuncio de casa.",
        toggleLabel: status.live ? "Detener" : "Iniciar en vivo",
        toggleTo: !status.live,
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
