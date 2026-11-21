import { z } from "zod";

const ProviderSchema = z.object({ configured: z.boolean() });

export const InputHealthSchema = z.object({
    // Which paid generators have keys (app/bootstrap/readiness.py); absent on old backends.
    ads: z
        .object({
            providers: z.object({ ai_copy: ProviderSchema, voice: ProviderSchema }).partial(),
        })
        .optional(),
    inputs: z.record(
        z.string(),
        z.object({
            camera_recent: z.boolean(),
            speech_enabled: z.boolean(),
            speech_recent: z.boolean(),
            last_utterance_at: z.string().nullable(),
            last_keyterm_count: z.number().int().nonnegative().nullable().default(null),
            player_connected: z.boolean(),
        }),
    ),
});

export function inputLabels(health: z.infer<typeof InputHealthSchema>, screen: string) {
    const input = health.inputs[screen];
    const count = input?.last_keyterm_count;
    const received =
        count === null || count === undefined
            ? "Voz recibida"
            : count === 0
              ? "Voz recibida · sin términos reconocidos"
              : `Voz recibida · ${count} ${count === 1 ? "término reconocido" : "términos reconocidos"}`;
    return {
        camera: input?.camera_recent ? "Cámara recibiendo datos" : "Cámara sin datos recientes",
        speech: !input?.speech_enabled
            ? "Voz deshabilitada"
            : input.speech_recent
              ? received
              : "Voz habilitada · esperando frase",
        player: input?.player_connected ? "Pantalla conectada" : "Pantalla desconectada",
    };
}

/** What "ad generation" means on this station: AI only where keys exist, else stock copy. */
export function generationLabel(health: z.infer<typeof InputHealthSchema>): {
    label: string;
    ai: boolean;
} {
    const providers = health.ads?.providers;
    const copy = providers?.ai_copy?.configured ?? false;
    const voice = providers?.voice?.configured ?? false;
    if (copy && voice) return { label: "IA: copy y voz configurados", ai: true };
    if (copy) return { label: "IA: copy configurado · sin voz", ai: true };
    return { label: "Sin claves de IA: anuncios con copy aprobado de stock", ai: false };
}
