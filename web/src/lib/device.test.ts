import { afterEach, expect, it, vi } from "vitest";
import { screenProtocols, screenToken } from "./device";

afterEach(() => {
    window.localStorage.clear();
});

const at = (search: string) => ({ search, pathname: "/screen", hash: "" });

it("takes the token from the URL once, then drops it from the address bar", () => {
    const history = { replaceState: vi.fn() };
    expect(screenToken(at("?id=demo-01&token=abc123"), history)).toBe("abc123");
    expect(history.replaceState).toHaveBeenCalledWith(null, "", "/screen?id=demo-01");
    // The next load has no token in the URL: the kiosk remembers it.
    expect(screenToken(at("?id=demo-01"), { replaceState: vi.fn() })).toBe("abc123");
});

it("without a token the player is view-only (no subprotocols)", () => {
    expect(screenToken(at("?id=demo-01"), { replaceState: vi.fn() })).toBeNull();
    expect(screenProtocols(null)).toBeUndefined();
});

it("a token that isn't a valid header token is never sent or kept", () => {
    expect(screenToken(at("?token=bad%20token"), { replaceState: vi.fn() })).toBeNull();
    expect(window.localStorage.getItem("team18.screenToken")).toBeNull();
});

it("offers the token in the subprotocol list, never in the URL", () => {
    expect(screenProtocols("abc123")).toEqual(["team18", "team18.token.abc123"]);
});
