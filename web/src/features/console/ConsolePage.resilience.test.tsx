import { ConsoleOutboundSchema, type ConsoleOutbound } from "@/contract";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
    consoleReducer,
    describe as describeMessage,
    initialConsoleState,
    RECEIPT_WINDOW,
} from "./console-state";
import { ConsolePage } from "./ConsolePage";
import type * as WhyModule from "./why";

/** Live-demo failure modes of the operator console: one broken section never blanks the page. */

vi.mock("@/lib/ws/typed-socket", () => ({
    TypedSocket: class {
        start() {}
        stop() {}
    },
}));
// A render-time bug in one section ("Por qué este aviso").
vi.mock("./why", async (original) => ({
    ...(await original<typeof WhyModule>()),
    explain: () => {
        throw new Error("render bug");
    },
}));

let root: Root;
let host: HTMLDivElement;
beforeEach(async () => {
    vi.useFakeTimers();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubGlobal(
        "fetch",
        vi.fn(() => Promise.resolve(new Response("down", { status: 503 }))),
    );
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => {
        root.render(<ConsolePage />);
        await vi.advanceTimersByTimeAsync(20);
    });
});
afterEach(async () => {
    await act(async () => {
        root.unmount();
        await Promise.resolve();
    });
    host.remove();
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

it("a section that throws is contained; the rest of the console stays up", () => {
    expect(host.textContent).toContain("Lo que ve la cámara");
    expect(host.textContent).toContain("Lo que ve la gente");
    expect(host.textContent).toContain("Sesión");
    expect(host.textContent).toContain("No se pudo mostrar esta sección.");
});

it("the session's charged total keeps counting past the receipt window", () => {
    let state = initialConsoleState;
    for (let i = 0; i < RECEIPT_WINDOW + 5; i++) {
        const message = ConsoleOutboundSchema.parse({
            type: "console.receipt",
            v: 1,
            screen_id: "demo-01",
            creative_id: `c${String(i)}`,
            auction_id: "a1",
            people: 2,
            looking: 2,
            played_s: "4",
            attention_seconds: "8",
            charges: [],
            total: { amount: "100", currency: "CLP" },
            filtered: [],
        });
        state = consoleReducer(state, { type: "message", message });
    }
    const view = state.screens["demo-01"]!;
    expect(view.receipts).toHaveLength(RECEIPT_WINDOW);
    expect(view.billed).toBe(100 * (RECEIPT_WINDOW + 5));
});

it("the timeline writes a charged receipt in CLP format", () => {
    const receipt = {
        type: "console.receipt",
        total: { amount: "12345", currency: "CLP" },
    } as unknown as ConsoleOutbound;
    expect(describeMessage(receipt)?.text).toBe("Recibo: $12.345 cobrado");
});
