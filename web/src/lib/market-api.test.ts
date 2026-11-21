import { afterEach, expect, it, vi } from "vitest";
import { setBidMultiplier } from "./market-api";

afterEach(() => {
    vi.unstubAllGlobals();
});

it("sends decimal strings with a sequence newer than the observed market clock", async () => {
    const floor = Date.now() + 3600000;
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ outcome: "applied" })));
    vi.stubGlobal("fetch", fetcher);
    const seq = await setBidMultiplier("campaign", "4.8", floor);
    const options = fetcher.mock.calls[0]![1] as RequestInit;
    expect(JSON.parse(options.body as string)).toMatchObject({ multiplier: "4.8", seq });
    expect(seq).toBeGreaterThan(floor);
});

it("reports a superseded bid as a failure rather than falsely confirming it", async () => {
    vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(new Response(JSON.stringify({ outcome: "stale" }))),
    );
    await expect(setBidMultiplier("campaign", "1.0")).rejects.toThrow("superseded");
});
