import type { CreativeSpec } from "@/contract";
import { assetUrl } from "@/lib/config";

/**
 * Decode required brand logos before showing the creative. Optional media
 * loads in the mounted back layer and may appear when ready; it never gates
 * the copy/price/logo swap. Voice preparation has its own on-air lifecycle.
 */

const IMAGE_TIMEOUT_MS = 1_500;

function withTimeout<T>(promise: Promise<T>, ms: number, signal: AbortSignal): Promise<T> {
    return new Promise<T>((resolve, reject) => {
        const cleanup = () => {
            clearTimeout(timer);
            signal.removeEventListener("abort", abort);
        };
        const timer = setTimeout(() => {
            cleanup();
            reject(new Error(`timed out after ${ms} ms`));
        }, ms);
        const abort = () => {
            cleanup();
            reject(new DOMException("aborted", "AbortError"));
        };
        signal.addEventListener("abort", abort, { once: true });
        promise.then(
            (value) => {
                cleanup();
                resolve(value);
            },
            (error: unknown) => {
                cleanup();
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

export async function preloadCreative(spec: CreativeSpec, signal: AbortSignal): Promise<void> {
    if (signal.aborted) throw new DOMException("aborted", "AbortError");
    const logos = new Set(spec.slots.map((slot) => slot.logo_url));
    await Promise.all(
        [...logos].map((url) => withTimeout(decodeImage(url), IMAGE_TIMEOUT_MS, signal)),
    );
}
