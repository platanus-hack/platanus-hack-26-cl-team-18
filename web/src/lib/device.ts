/**
 * Device tokens (app/api/device_auth.py): the screen token and the opt-in
 * console token (CONSOLE_TOKEN), both taken once from `?token=` on their page.
 *
 * The screen token (app/api/device_auth.py). Without it a player only watches:
 * it gets every creative, but its playback reports never bill. The token comes
 * once as `/screen?token=…`, is then removed from the address bar and kept in
 * this browser (the kiosk), and travels in the WebSocket subprotocol list, the
 * one handshake header a browser can set. It is never in the socket URL.
 */

const REMEMBERED = "team18.screenToken";
const REMEMBERED_CONSOLE = "team18.consoleToken";
// Subprotocol values must be HTTP tokens; `openssl rand -hex 16` fits.
const SAFE_TOKEN = /^[A-Za-z0-9_-]{1,256}$/;

const SCREEN_SUBPROTOCOL = "team18";

type Place = Pick<Location, "search" | "pathname" | "hash">;
type Navigation = Pick<History, "replaceState">;

/** The token from `?token=` (then forgotten by the URL) or the one this browser kept. */
export function screenToken(
    location: Place = window.location,
    history: Navigation = window.history,
): string | null {
    return rememberedToken(REMEMBERED, location, history);
}

/**
 * The console token: `/console?token=…` once, then kept like the screen token.
 * Null when the station runs without CONSOLE_TOKEN (the console socket is open).
 */
export function consoleToken(
    location: Place = window.location,
    history: Navigation = window.history,
): string | null {
    return rememberedToken(REMEMBERED_CONSOLE, location, history);
}

/** What `/ws/console` offers at the handshake (lib/ws/typed-socket.ts adds it). */
export const consoleProtocols = (token: string | null): readonly string[] | undefined =>
    screenProtocols(token);

function rememberedToken(storageKey: string, location: Place, history: Navigation): string | null {
    const params = new URLSearchParams(location.search);
    const fromUrl = params.get("token");
    if (fromUrl !== null) {
        params.delete("token");
        const query = params.toString();
        try {
            history.replaceState(
                null,
                "",
                `${location.pathname}${query ? `?${query}` : ""}${location.hash}`,
            );
        } catch {
            // A sandboxed frame can refuse; the socket still works.
        }
        if (SAFE_TOKEN.test(fromUrl)) {
            try {
                window.localStorage.setItem(storageKey, fromUrl);
            } catch {
                // Blocked storage: this load still bills; a reload needs the URL again.
            }
            return fromUrl;
        }
    }
    try {
        const kept = window.localStorage.getItem(storageKey);
        return kept !== null && SAFE_TOKEN.test(kept) ? kept : null;
    } catch {
        return null;
    }
}

/** What the player offers at the handshake; undefined means a view-only player. */
export function screenProtocols(token: string | null): readonly string[] | undefined {
    return token === null
        ? undefined
        : [SCREEN_SUBPROTOCOL, `${SCREEN_SUBPROTOCOL}.token.${token}`];
}
