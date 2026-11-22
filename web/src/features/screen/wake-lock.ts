/**
 * Keep the TV awake. A kiosk laptop's display sleeps after the OS idle timeout
 * (nobody touches it), and only a playing video holds Chromium's own wake lock:
 * a house ad or a photo creative would let the screen go black mid-demo.
 *
 * The Screen Wake Lock API needs a secure context (localhost counts) and the
 * browser drops the lock whenever the page is hidden, so it is re-requested
 * on every return to visible. Unsupported or refused: a no-op, never an error.
 */
interface WakeLockSentinelLike {
    readonly released: boolean;
    release(): Promise<void>;
}
export interface WakeLockLike {
    request(type: "screen"): Promise<WakeLockSentinelLike>;
}

export function holdScreenAwake(
    wakeLock: WakeLockLike | undefined = (navigator as { wakeLock?: WakeLockLike }).wakeLock,
    doc: Document = document,
): () => void {
    if (wakeLock === undefined) return () => {};
    let sentinel: WakeLockSentinelLike | null = null;
    let stopped = false;
    const acquire = () => {
        if (stopped || doc.visibilityState !== "visible") return;
        if (sentinel !== null && !sentinel.released) return;
        wakeLock.request("screen").then(
            (lock) => {
                if (stopped) void lock.release().catch(() => {});
                else sentinel = lock;
            },
            () => {
                // Refused (no permission, battery saver): the screen still plays.
            },
        );
    };
    doc.addEventListener("visibilitychange", acquire);
    acquire();
    return () => {
        stopped = true;
        doc.removeEventListener("visibilitychange", acquire);
        if (sentinel !== null) void sentinel.release().catch(() => {});
    };
}
