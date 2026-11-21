import { describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { backoffDelay, TypedSocket, type ConnectionState } from "./typed-socket";

class FakeSocket {
    static OPEN = 1;
    readyState = 0;
    sent: string[] = [];
    onopen: (() => void) | null = null;
    onmessage: ((e: { data: unknown }) => void) | null = null;
    onclose: (() => void) | null = null;
    onerror: (() => void) | null = null;
    send(data: string) {
        this.sent.push(data);
    }
    close() {
        this.onclose?.();
    }
    open() {
        this.readyState = 1;
        this.onopen?.();
    }
    emit(data: unknown) {
        this.onmessage?.({ data });
    }
}

const Schema = z.strictObject({ type: z.literal("ping"), n: z.number() });

function setup() {
    vi.stubGlobal("WebSocket", { OPEN: 1 });
    const sockets: FakeSocket[] = [];
    const messages: z.infer<typeof Schema>[] = [];
    const states: ConnectionState[] = [];
    const socket = new TypedSocket({
        url: "ws://test",
        schema: Schema,
        onMessage: (m) => messages.push(m),
        onStateChange: (s) => states.push(s),
        createSocket: () => {
            const s = new FakeSocket();
            sockets.push(s);
            return s as unknown as WebSocket;
        },
        random: () => 0.5,
    });
    return { socket, sockets, messages, states };
}

describe("TypedSocket", () => {
    it("delivers only frames that pass the schema", () => {
        const { socket, sockets, messages } = setup();
        socket.start();
        sockets[0]!.open();
        sockets[0]!.emit(JSON.stringify({ type: "ping", n: 1 }));
        sockets[0]!.emit(JSON.stringify({ type: "ping", n: "x" }));
        sockets[0]!.emit("{not json");
        sockets[0]!.emit(new ArrayBuffer(4));
        expect(messages).toEqual([{ type: "ping", n: 1 }]);
        expect(socket.stats).toMatchObject({ received: 1, invalid: 3 });
    });

    it("reconnects with backoff after a close and resets the attempt on open", () => {
        vi.useFakeTimers();
        const { socket, sockets, states } = setup();
        socket.start();
        sockets[0]!.open();
        sockets[0]!.close();
        expect(states.at(-1)).toMatchObject({ status: "reconnecting", attempt: 1 });
        vi.runOnlyPendingTimers();
        expect(sockets).toHaveLength(2);
        sockets[1]!.open();
        expect(states.at(-1)?.status).toBe("open");
        socket.stop();
        expect(states.at(-1)?.status).toBe("closed");
        vi.useRealTimers();
    });

    it("send reports whether the frame went out", () => {
        const { socket, sockets } = setup();
        socket.start();
        expect(socket.send({} as never)).toBe(false);
        sockets[0]!.open();
        expect(socket.send({} as never)).toBe(true);
    });

    it("backoff is capped with equal jitter", () => {
        expect(backoffDelay(1, 250, 5000, 0)).toBe(125);
        expect(backoffDelay(1, 250, 5000, 1)).toBe(250);
        expect(backoffDelay(20, 250, 5000, 1)).toBe(5000);
    });
});

describe("socket lifecycle races", () => {
    it("start is idempotent while connecting or connected", () => {
        const { socket, sockets } = setup();
        socket.start();
        socket.start();
        expect(sockets).toHaveLength(1);
        sockets[0]!.open();
        socket.start();
        expect(sockets).toHaveLength(1);
        socket.stop();
    });

    it("ignores late callbacks from a stopped connection after restart", () => {
        vi.useFakeTimers();
        try {
            const { socket, sockets, messages } = setup();
            socket.start();
            const retired = sockets[0]!;
            socket.stop();
            socket.start();
            sockets[1]!.open();
            retired.open();
            retired.emit(JSON.stringify({ type: "ping", n: 99 }));
            retired.close();
            expect(socket.state.status).toBe("open");
            expect(messages).toEqual([]);
            vi.runOnlyPendingTimers();
            expect(sockets).toHaveLength(2);
            socket.stop();
        } finally {
            vi.useRealTimers();
        }
    });

    it("does not open another socket when start is called during reconnect backoff", () => {
        vi.useFakeTimers();
        try {
            const { socket, sockets } = setup();
            socket.start();
            sockets[0]!.close();
            socket.start();
            expect(sockets).toHaveLength(1);
            vi.runOnlyPendingTimers();
            expect(sockets).toHaveLength(2);
            socket.stop();
        } finally {
            vi.useRealTimers();
        }
    });
});
