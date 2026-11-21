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

export type InputHealth = z.infer<typeof InputHealthSchema>;

/** What "ad generation" means on this station: AI only where keys exist, else stock copy. */
export function generationLabel(health: InputHealth): {
    label: string;
    ai: boolean;
} {
    const providers = health.ads?.providers;
    const copy = providers?.ai_copy?.configured ?? false;
    const voice = providers?.voice?.configured ?? false;
    if (copy && voice) return { label: "IA: copy y voz", ai: true };
    if (copy) return { label: "IA: copy, sin voz", ai: true };
    return { label: "Copy de stock", ai: false };
}
