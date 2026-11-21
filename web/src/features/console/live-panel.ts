import type { LiveStatus } from "@/contract";
import type { Tone } from "@/shared/ui/badge";

/**
 * What the "En vivo" panel shows for a switch position. Pure, so every state the
 * operator can meet on demo day is unit-tested. Status is never color alone.
 */

export interface Chip {
    readonly key: "camera" | "mic" | "ai";
    readonly label: string;
    readonly tone: Tone;
}

export interface LivePanelView {
    readonly headline: string;
    readonly detail: string;
    readonly tone: Tone;
    readonly toggleLabel: string;
    readonly toggleTo: boolean;
    readonly chips: readonly Chip[];
    /** null: this screen has no speech opt-in (SPEECH_MODE_SCREENS), so no mic control. */
    readonly mic: { readonly label: string; readonly pressed: boolean } | null;
    readonly warnings: readonly string[];
}

export function livePanelView(status: LiveStatus): LivePanelView {
    const vision = status.agents.includes("vision");
    const speech = status.agents.includes("speech");
    const warnings: string[] = [];
    if (status.live && !vision)
        warnings.push(
            "No hay agente de cámara conectado: la pantalla mostrará el anuncio de casa.",
        );
    if (status.live && status.mic && status.speech_opt_in && !speech)
        warnings.push("El micrófono está activado, pero el agente de voz no está conectado.");

    const camera: Chip = !vision
        ? { key: "camera", label: "Cámara desconectada", tone: status.live ? "danger" : "warning" }
        : status.sensing
          ? { key: "camera", label: "Cámara analizando", tone: "success" }
          : { key: "camera", label: "Cámara en espera", tone: "neutral" };

    const mic: Chip = !status.speech_opt_in
        ? { key: "mic", label: "Voz no habilitada en esta pantalla", tone: "neutral" }
        : status.listening
          ? {
                key: "mic",
                label: speech ? "Micrófono escuchando" : "Micrófono sin agente",
                tone: speech ? "info" : "warning",
            }
          : {
                key: "mic",
                label: status.live && !status.mic ? "Micrófono silenciado" : "Micrófono cerrado",
                tone: "neutral",
            };

    const ai: Chip = status.ai_generation
        ? { key: "ai", label: "Generación de anuncios activa", tone: "success" }
        : { key: "ai", label: "Generación de anuncios en pausa", tone: "neutral" };

    return {
        headline: status.live ? "En vivo" : "En espera",
        detail: status.live
            ? "La pantalla ve, escucha y subasta en tiempo real."
            : "Nada se analiza ni se genera. La pantalla muestra el anuncio de casa.",
        tone: status.live ? "success" : "neutral",
        toggleLabel: status.live ? "Detener" : "Iniciar en vivo",
        toggleTo: !status.live,
        chips: [camera, mic, ai],
        mic: status.speech_opt_in
            ? {
                  label: status.mic ? "Silenciar micrófono" : "Activar micrófono",
                  pressed: status.mic,
              }
            : null,
        warnings,
    };
}
