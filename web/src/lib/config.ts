/**
 * Where the backend lives. Default: same origin (Vite proxies /ws, /health and
 * /media to the backend in dev; in kiosk mode the backend serves the build).
 * Override with VITE_BACKEND_URL, e.g. http://192.168.1.20:8000.
 */
const configured = (import.meta.env["VITE_BACKEND_URL"] as string | undefined)?.replace(/\/$/, "");

export const backendHttpUrl: string = configured ?? window.location.origin;

export const backendWsUrl: string = backendHttpUrl.replace(/^http/, "ws");

/** Resolve a contract URL (`/media/...`, `/kits/...` or absolute) to a loadable URL. */
export function assetUrl(url: string): string {
    return /^(https?:|data:|blob:)/.test(url)
        ? url
        : `${backendHttpUrl}${url.startsWith("/") ? "" : "/"}${url}`;
}

/** The screen id this player is: `/screen?id=mall-01` (default `demo-01`). */
export function screenIdFromLocation(search: string = window.location.search): string {
    const id = new URLSearchParams(search).get("id") ?? "demo-01";
    return /^[A-Za-z0-9_.:-]{1,64}$/.test(id) ? id : "demo-01";
}
