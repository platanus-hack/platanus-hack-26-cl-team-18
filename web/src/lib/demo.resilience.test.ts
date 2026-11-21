import { afterEach, expect, it, vi } from "vitest";
import { rememberDemoKeyIfAccepted, rememberedDemoKey } from "./demo";

/** A mistyped /demo/<key> must never replace the key this browser already uses. */

afterEach(() => {
    window.localStorage.clear();
});

const answering = (status: number) =>
    vi.fn((_input: string, _init?: RequestInit) => Promise.resolve(new Response("{}", { status })));

it("a key the backend rejects never replaces the working one", async () => {
    window.localStorage.setItem("team18.demoKey", "good-key");
    expect(await rememberDemoKeyIfAccepted("typo-key", answering(404))).toBe(false);
    expect(rememberedDemoKey()).toBe("good-key");
});

it("a backend that is down doesn't replace it either", async () => {
    window.localStorage.setItem("team18.demoKey", "good-key");
    const down = vi.fn(() => Promise.reject(new Error("offline")));
    expect(await rememberDemoKeyIfAccepted("other-key", down)).toBe(false);
    expect(rememberedDemoKey()).toBe("good-key");
});

it("a key the backend accepts is remembered, checked with the key header", async () => {
    const fetcher = answering(200);
    expect(await rememberDemoKeyIfAccepted("new-key", fetcher)).toBe(true);
    expect(rememberedDemoKey()).toBe("new-key");
    const init = fetcher.mock.calls[0]?.[1];
    expect(new Headers(init?.headers).get("X-Demo-Key")).toBe("new-key");
});
