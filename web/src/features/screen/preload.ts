import type { CreativeSpec } from "@/contract";
import { assetUrl } from "@/lib/config";

/**
 * Get every asset of a creative decoded before it is shown ("never
 * mount-and-show"). Logos are required: a creative without its real logo must
 * not air. Hero images and video are best effort: on failure the layout
 * renders without them rather than blocking the swap.
 */

const IMAGE_TIMEOUT_MS = 1_500;
const VIDEO_TIMEOUT_MS = 3_000;

function withTimeout<T>(promise: Promise<T>, ms: number, signal: AbortSignal): Promise<T> {
    return new Promise<T>((resolve, reject) => {
        const timer = setTimeout(() => {
            reject(new Error(`timed out after ${ms} ms`));
        }, ms);
        const abort = () => {
            clearTimeout(timer);
            reject(new DOMException("aborted", "AbortError"));
        };
        signal.addEventListener("abort", abort, { once: true });
        promise.then(
            (value) => {
                clearTimeout(timer);
                signal.removeEventListener("abort", abort);
                resolve(value);
            },
            (error: unknown) => {
                clearTimeout(timer);
                signal.removeEventListener("abort", abort);
                reject(error instanceof Error ? error : new Error(String(error)));
            },
        );
    });
}

function decodeImage(url: string): Promise<void> {
    const img = new Image();
    img.decoding = "async";
    img.src = assetUrl(url);
    return img.decode();
}

function bufferVideo(url: string): Promise<void> {
    return new Promise<void>((resolve, reject) => {
        const video = document.createElement("video");
        video.muted = true;
        video.preload = "auto";
        video.oncanplaythrough = () => {
            resolve();
        };
        video.onerror = () => {
            reject(new Error(`video failed: ${url}`));
        };
        video.src = assetUrl(url);
        video.load();
    });
}

/** Optional audio preparation (the voice player decodes the voice-over into a buffer). */
export type PrepareAudio = (url: string, signal: AbortSignal) => Promise<void>;

export async function preloadCreative(
    spec: CreativeSpec,
    signal: AbortSignal,
    prepareAudio?: PrepareAudio,
): Promise<void> {
    const required = spec.slots.map((slot) =>
        withTimeout(decodeImage(slot.logo_url), IMAGE_TIMEOUT_MS, signal),
    );
    const optional: Promise<unknown>[] = spec.slots
        .flatMap((slot) => (slot.hero_image_url ? [slot.hero_image_url] : []))
        .map((url) => withTimeout(decodeImage(url), IMAGE_TIMEOUT_MS, signal));
    if (spec.video_url)
        optional.push(withTimeout(bufferVideo(spec.video_url), VIDEO_TIMEOUT_MS, signal));

    if (spec.voiceover_url && prepareAudio)
        optional.push(
            withTimeout(prepareAudio(spec.voiceover_url, signal), VIDEO_TIMEOUT_MS, signal),
        );

    await Promise.all(required);
    await Promise.allSettled(optional);
}
