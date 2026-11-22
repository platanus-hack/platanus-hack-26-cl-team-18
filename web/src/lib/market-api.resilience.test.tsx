import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { setBidMultiplier, useBook } from "./market-api";

/** A hung backend: polling every second must not pile up requests. */

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

function Probe() {
    useBook(1000);
    return null;
}

it("keeps at most one book request in flight while the backend hangs", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const signals: AbortSignal[] = [];
    vi.stubGlobal(
        "fetch",
        vi.fn((_input: string, init?: RequestInit) => {
            if (init?.signal) signals.push(init.signal);
            return new Promise<Response>(() => {}); // never answers
        }),
    );
    const host = document.createElement("div");
    const root = createRoot(host);
    await act(async () => {
        root.render(<Probe />);
        await Promise.resolve();
    });
    await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(signals.length).toBeGreaterThan(1); // it keeps trying
    expect(signals.filter((signal) => !signal.aborted)).toHaveLength(1);
    await act(async () => {
        root.unmount();
        await Promise.resolve();
    });
    expect(signals.every((signal) => signal.aborted)).toBe(true);
});

it("waits for a slow backend instead of aborting every answer", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const book = { type: "console.book", v: 1, screen_id: "demo-01", lines: [] };
    let aborted = 0;
    vi.stubGlobal(
        "fetch",
        vi.fn(
            (_input: string, init?: RequestInit) =>
                new Promise<Response>((resolve) => {
                    init?.signal?.addEventListener("abort", () => aborted++);
                    setTimeout(() => {
                        resolve(new Response(JSON.stringify(book)));
                    }, 1_500);
                }),
        ),
    );
    let seen: unknown = null;
    function Reader({ onBook }: { onBook: (book: unknown) => void }) {
        onBook(useBook(1000).book);
        return null;
    }
    const root = createRoot(document.createElement("div"));
    await act(async () => {
        root.render(
            <Reader
                onBook={(next) => {
                    seen = next;
                }}
            />,
        );
        await Promise.resolve();
    });
    await act(async () => {
        await vi.advanceTimersByTimeAsync(4_000);
    });
    expect(seen).toMatchObject({ type: "console.book" });
    expect(aborted).toBe(0);
    await act(async () => {
        root.unmount();
        await Promise.resolve();
    });
});

it("a bid POST that hangs on a stalled tunnel gives up, so the slider's next move is sent", async () => {
    // A judge's phone on bad 4G: the request never answers. Without a timeout the
    // BidQueue waits on it forever and every later slider move is silently lost.
    vi.useFakeTimers();
    const signals: AbortSignal[] = [];
    vi.stubGlobal(
        "fetch",
        vi.fn(
            (_input: string, init?: RequestInit) =>
                new Promise<Response>((_resolve, reject) => {
                    if (init?.signal) signals.push(init.signal);
                    init?.signal?.addEventListener("abort", () => {
                        reject(new DOMException("aborted", "AbortError"));
                    });
                }),
        ),
    );
    const outcome = setBidMultiplier("c-1", "2.0").then(
        () => "sent",
        () => "gave up",
    );
    await vi.advanceTimersByTimeAsync(10_000);
    expect(await outcome).toBe("gave up");
    expect(signals).toHaveLength(1);
    expect(signals[0]!.aborted).toBe(true);
});
