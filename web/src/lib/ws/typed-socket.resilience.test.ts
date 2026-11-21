import { afterEach, expect, it, vi } from "vitest";
import { z } from "zod";
import { TypedSocket, type ConnectionState } from "./typed-socket";

/** A handshake that never completes (a stalled tunnel or proxy): no open, no close. */
class StalledSocket {
    readyState = 0;
    closed = false;
    onopen: (() => void) | null = null;
    onmessage: ((e: { data: unknown }) => void) | null = null;
    onclose: ((e?: { code: number }) => void) | null = null;
    onerror: (() => void) | null = null;
    send() {}
    close() {
        this.closed = true;
    }
    open() {
        this.readyState = 1;
        this.onopen?.();
    }
}

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

function setup() {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", { OPEN: 1 });
    const sockets: StalledSocket[] = [];
    const states: ConnectionState[] = [];
    const socket = new TypedSocket({
        url: "ws://test",
        schema: z.strictObject({ type: z.literal("ping") }),
        onMessage: () => {},
        onStateChange: (s) => states.push(s),
        createSocket: () => {
            const s = new StalledSocket();
            sockets.push(s);
            return s as unknown as WebSocket;
        },
        random: () => 0.5,
        connectTimeoutMs: 5_000,
    });
    return { socket, sockets, states };
}

it("gives up on a handshake that never completes and reconnects", () => {
    const { socket, sockets, states } = setup();
    socket.start();
    vi.advanceTimersByTime(4_999);
    expect(sockets).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets[0]!.closed).toBe(true);
    expect(states.at(-1)?.status).toBe("reconnecting");
    vi.advanceTimersByTime(10_000);
    expect(sockets.length).toBeGreaterThanOrEqual(2);
    socket.stop();
});

it("an open connection is never timed out", () => {
    const { socket, sockets, states } = setup();
    socket.start();
    sockets[0]!.open();
    vi.advanceTimersByTime(60_000);
    expect(sockets).toHaveLength(1);
    expect(sockets[0]!.closed).toBe(false);
    expect(states.at(-1)?.status).toBe("open");
    socket.stop();
});

it("stop() during a handshake cancels the timeout", () => {
    const { socket, sockets, states } = setup();
    socket.start();
    socket.stop();
    vi.advanceTimersByTime(60_000);
    expect(sockets).toHaveLength(1);
    expect(states.at(-1)?.status).toBe("closed");
});
