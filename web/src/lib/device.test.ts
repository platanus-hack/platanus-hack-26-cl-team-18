import { afterEach, expect, it, vi } from "vitest";
import { consoleProtocols, consoleToken, screenProtocols, screenToken } from "./device";

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

it("the console token is kept apart from the screen token", () => {
    const console = { search: "?screen=demo-01&token=cons123", pathname: "/console", hash: "" };
    const history = { replaceState: vi.fn() };
    expect(consoleToken(console, history)).toBe("cons123");
    expect(history.replaceState).toHaveBeenCalledWith(null, "", "/console?screen=demo-01");
    expect(window.localStorage.getItem("team18.consoleToken")).toBe("cons123");
    expect(window.localStorage.getItem("team18.screenToken")).toBeNull();
    expect(consoleProtocols("cons123")).toEqual(["team18", "team18.token.cons123"]);
    expect(consoleProtocols(null)).toBeUndefined();
});
