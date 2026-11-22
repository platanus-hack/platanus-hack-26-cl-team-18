import { afterEach, describe, expect, it, vi } from "vitest";
import { setLive } from "./live-api";

const STATUS = {
    v: 1,
    type: "live.status",
    screen_id: "demo-01",
    revision: 1,
    live: true,
    mic: false,
    speech_opt_in: false,
    sensing: true,
    listening: false,
    ai_generation: true,
    agents: [],
    changed_at: "2026-11-21T15:00:00+00:00",
};

function stubFetch() {
    const fetcher = vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify(STATUS), { status: 200 })),
    );
    vi.stubGlobal("fetch", fetcher);
    return fetcher;
}

function sentHeaders(fetcher: ReturnType<typeof stubFetch>): Record<string, string> {
    const init = (fetcher.mock.calls[0] as unknown as [string, RequestInit])[1];
    return init.headers as Record<string, string>;
}

describe("the live switch request", () => {
    afterEach(() => {
        vi.unstubAllGlobals();
        window.localStorage.clear();
    });

    it("carries the demo key this browser remembered", async () => {
        window.localStorage.setItem("team18.demoKey", "remembered");
        const fetcher = stubFetch();
        await setLive("demo-01", { live: true });
        expect(sentHeaders(fetcher)["X-Demo-Key"]).toBe("remembered");
    });

    it("works without a key (the station itself needs none)", async () => {
        const fetcher = stubFetch();
        const status = await setLive("demo-01", { live: true });
        expect(status.live).toBe(true);
        expect(sentHeaders(fetcher)["X-Demo-Key"]).toBeUndefined();
    });
});
