import { afterEach, expect, it, vi } from "vitest";
import { z } from "zod";
import { TypedSocket, type ConnectionState } from "./typed-socket";

/**
 * A link that died without a reset (venue Wi-Fi, a half-open tunnel): the socket
 * stays "open" and simply goes quiet. The backend sends a heartbeat every 5 s
 * (app/bootstrap/container.py), so silence past `idleTimeoutMs` means dead.
 */
class QuietSocket {
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
    frame(data: unknown) {
        this.onmessage?.({ data });
    }
}

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

function setup(idleTimeoutMs?: number) {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", { OPEN: 1 });
    const sockets: QuietSocket[] = [];
    const states: ConnectionState[] = [];
    const socket = new TypedSocket({
        url: "ws://test",
        schema: z.strictObject({ type: z.literal("ping") }),
        onMessage: () => {},
        onStateChange: (s) => states.push(s),
        createSocket: () => {
            const s = new QuietSocket();
            sockets.push(s);
            return s as unknown as WebSocket;
        },
        random: () => 0.5,
        ...(idleTimeoutMs === undefined ? {} : { idleTimeoutMs }),
    });
    return { socket, sockets, states };
}

it("a silently dead open link is closed and reconnected, and says so", () => {
    const { socket, sockets, states } = setup(15_000);
    socket.start();
    sockets[0]!.open();
    vi.advanceTimersByTime(14_999);
    expect(states.at(-1)?.status).toBe("open");
    vi.advanceTimersByTime(1);
    expect(sockets[0]!.closed).toBe(true);
    expect(states.at(-1)?.status).toBe("reconnecting");
    vi.advanceTimersByTime(1_000);
    expect(sockets).toHaveLength(2);
    socket.stop();
});

it("every frame, even one that fails validation, proves the link is alive", () => {
    const { socket, sockets } = setup(15_000);
    socket.start();
    sockets[0]!.open();
    for (let i = 0; i < 10; i += 1) {
        vi.advanceTimersByTime(10_000);
        sockets[0]!.frame(i % 2 ? JSON.stringify({ type: "ping" }) : "not json");
    }
    expect(sockets).toHaveLength(1);
    expect(sockets[0]!.closed).toBe(false);
    socket.stop();
});

it("a late frame or close from the abandoned socket never revives it", () => {
    const { socket, sockets, states } = setup(15_000);
    socket.start();
    sockets[0]!.open();
    vi.advanceTimersByTime(15_000);
    sockets[0]!.frame(JSON.stringify({ type: "ping" }));
    sockets[0]!.onclose?.({ code: 1006 });
    expect(states.at(-1)?.status).toBe("reconnecting");
    vi.advanceTimersByTime(1_000);
    expect(sockets).toHaveLength(2);
    socket.stop();
});

it("stop() cancels the idle watchdog", () => {
    const { socket, sockets, states } = setup(15_000);
    socket.start();
    sockets[0]!.open();
    socket.stop();
    vi.advanceTimersByTime(60_000);
    expect(sockets).toHaveLength(1);
    expect(states.at(-1)?.status).toBe("closed");
});

it("without idleTimeoutMs a quiet open link is kept (sockets with no heartbeat)", () => {
    const { socket, sockets } = setup();
    socket.start();
    sockets[0]!.open();
    vi.advanceTimersByTime(120_000);
    expect(sockets).toHaveLength(1);
    socket.stop();
});
