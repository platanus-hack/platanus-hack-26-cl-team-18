import { z } from "zod";

export const InputHealthSchema = z.object({
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
