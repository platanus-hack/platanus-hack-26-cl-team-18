/**
 * Fullscreen for the TV: `F` or a double-click toggles it. Browsers allow
 * `requestFullscreen()` only inside a user gesture, so the page can't start
 * fullscreen by itself; a short hint says how, and never covers the ad for long.
 * (Kiosk Chromium, `--kiosk`, is already fullscreen and never shows the hint.)
 */
import { useEffect, useState } from "react";
import { ScreenPage } from "./ScreenPage";

const HINT_MS = 6_000;

const isFullscreen = (doc: Document) => doc.fullscreenElement !== null;

export function toggleFullscreen(doc: Document = document): void {
    const request = isFullscreen(doc)
        ? doc.exitFullscreen()
        : doc.documentElement.requestFullscreen({ navigationUI: "hide" });
    request.catch((error: unknown) => {
        console.warn(`[screen] fullscreen refused: ${String(error)}`);
    });
}

/** `F` toggles fullscreen unless a modifier is held (Ctrl+F still finds) or focus is in a field. */
export function isFullscreenKey(event: KeyboardEvent): boolean {
    if (event.ctrlKey || event.metaKey || event.altKey || event.repeat) return false;
    const target = event.target;
    if (
        target instanceof HTMLElement &&
        (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))
    ) {
        return false;
    }
    return event.key === "f" || event.key === "F";
}

export function useFullscreenShortcut(doc: Document = document): boolean {
    const [full, setFull] = useState(() => isFullscreen(doc));
    useEffect(() => {
        const onKey = (event: KeyboardEvent) => {
            if (!isFullscreenKey(event)) return;
            event.preventDefault();
            toggleFullscreen(doc);
        };
        const onDoubleClick = () => {
            toggleFullscreen(doc);
        };
        const onChange = () => {
            setFull(isFullscreen(doc));
        };
        doc.addEventListener("keydown", onKey);
        doc.addEventListener("dblclick", onDoubleClick);
        doc.addEventListener("fullscreenchange", onChange);
        return () => {
            doc.removeEventListener("keydown", onKey);
            doc.removeEventListener("dblclick", onDoubleClick);
            doc.removeEventListener("fullscreenchange", onChange);
        };
    }, [doc]);
    return full;
}

/** `/screen` with the fullscreen shortcut and its first-seconds hint. */
export function FullscreenScreenPage() {
    const full = useFullscreenShortcut();
    const [hint, setHint] = useState(true);
    useEffect(() => {
        const timer = setTimeout(() => {
            setHint(false);
        }, HINT_MS);
        return () => {
            clearTimeout(timer);
        };
    }, []);
    return (
        <>
            <ScreenPage />
            {hint && !full && (
                <div
                    role="status"
                    className="pointer-events-none fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full bg-ink/80 px-4 py-2 text-compact text-white"
                >
                    Pulsa <kbd className="font-mono font-semibold">F</kbd> o doble clic para
                    pantalla completa
                </div>
            )}
        </>
    );
}
