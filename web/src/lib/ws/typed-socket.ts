import type { z } from "zod";

/**
 * A WebSocket that never trusts the wire and never gives up.
 *
 * - Every inbound frame is size-capped, JSON-parsed and validated against the
 *   socket's zod schema (generated from the backend contract). Invalid frames
 *   are counted and dropped; they never reach UI code.
 * - A handshake that doesn't open within `connectTimeoutMs` (a stalled tunnel
 *   or proxy) is abandoned and retried; the browser itself may wait minutes.
 * - Reconnects forever with capped exponential backoff + jitter. The UI keeps
 *   showing the last good state while `status` is "reconnecting" (never blank).
 * - Connection status is a discriminated union, not boolean soup.
 */

export type ConnectionState =
    | { readonly status: "connecting"; readonly attempt: number }
    | { readonly status: "open"; readonly since: number }
    | { readonly status: "reconnecting"; readonly attempt: number; readonly retryInMs: number }
    | { readonly status: "closed" };

export interface SocketStats {
    readonly received: number;
    readonly invalid: number;
    readonly reconnects: number;
}

interface Options<In> {
    readonly url: string;
    /** WebSocket subprotocols (e.g. the screen token, lib/device.ts). */
    readonly protocols?: readonly string[] | undefined;
    readonly schema: z.ZodType<In>;
    readonly onMessage: (message: In) => void;
    readonly onStateChange?: (state: ConnectionState) => void;
    /** The server's close code (e.g. 4404: endpoint disabled), before any reconnect. */
    readonly onClose?: (code: number) => void;
    readonly maxFrameBytes?: number;
    readonly backoff?: { readonly baseMs: number; readonly maxMs: number };
    readonly connectTimeoutMs?: number;
    /** Injected for tests. */
    readonly createSocket?: (url: string, protocols?: readonly string[]) => WebSocket;
    readonly random?: () => number;
}

const DEFAULT_BACKOFF = { baseMs: 250, maxMs: 5_000 } as const;
const DEFAULT_CONNECT_TIMEOUT_MS = 8_000;

export function backoffDelay(
    attempt: number,
    baseMs: number,
    maxMs: number,
    random: number,
): number {
    const ceiling = Math.min(maxMs, baseMs * 2 ** Math.max(0, attempt - 1));
    return Math.round(ceiling / 2 + (ceiling / 2) * random); // "equal jitter"
}

export class TypedSocket<In, Out = never> {
    private socket: WebSocket | null = null;
    private retryTimer: ReturnType<typeof setTimeout> | null = null;
    private connectTimer: ReturnType<typeof setTimeout> | null = null;
    private attempt = 0;
    private stopped = true;
    private stats_: SocketStats = { received: 0, invalid: 0, reconnects: 0 };
    private state_: ConnectionState = { status: "connecting", attempt: 0 };

    constructor(private readonly options: Options<In>) {}

    get state(): ConnectionState {
        return this.state_;
    }

    get stats(): SocketStats {
        return this.stats_;
    }

    start(): void {
        if (!this.stopped) return;
        this.stopped = false;
        this.attempt = 0;
        this.connect();
    }

    stop(): void {
        this.stopped = true;
        if (this.retryTimer !== null) clearTimeout(this.retryTimer);
        this.retryTimer = null;
        this.clearConnectTimer();
        const socket = this.socket;
        this.socket = null; // Invalidate callbacks before close (which may fire synchronously).
        socket?.close(1000, "client stop");
        this.setState({ status: "closed" });
    }

    /** Returns false when not connected; callers decide whether that matters. */
    send(message: Out): boolean {
        if (this.socket?.readyState !== WebSocket.OPEN) return false;
        this.socket.send(JSON.stringify(message));
        return true;
    }

    private connect(): void {
        if (this.stopped || this.socket !== null || this.retryTimer !== null) return;
        this.setState({ status: "connecting", attempt: this.attempt });
        const create =
            this.options.createSocket ??
            ((url: string, protocols?: readonly string[]) =>
                new WebSocket(url, protocols ? [...protocols] : undefined));
        const socket = create(this.options.url, this.options.protocols);
        this.socket = socket;
        this.connectTimer = setTimeout(() => {
            this.connectTimer = null;
            if (this.stopped || this.socket !== socket) return;
            this.socket = null; // Invalidate callbacks before close.
            socket.close();
            this.options.onClose?.(1006);
            this.scheduleReconnect();
        }, this.options.connectTimeoutMs ?? DEFAULT_CONNECT_TIMEOUT_MS);

        socket.onopen = () => {
            if (this.stopped || this.socket !== socket) return;
            this.clearConnectTimer();
            this.attempt = 0;
            this.setState({ status: "open", since: Date.now() });
        };
        socket.onmessage = (event: MessageEvent) => {
            if (this.stopped || this.socket !== socket) return;
            this.handleFrame(event.data);
        };
        socket.onclose = (event?: CloseEvent) => {
            if (this.stopped || this.socket !== socket) return;
            this.socket = null;
            this.clearConnectTimer();
            this.options.onClose?.(event?.code ?? 1006);
            this.scheduleReconnect();
        };
        socket.onerror = () => {
            // onclose follows; reconnect is handled there.
        };
    }

    private clearConnectTimer(): void {
        if (this.connectTimer !== null) clearTimeout(this.connectTimer);
        this.connectTimer = null;
    }

    private handleFrame(data: unknown): void {
        const maxBytes = this.options.maxFrameBytes ?? 1_000_000;
        if (typeof data !== "string" || data.length > maxBytes) {
            this.countInvalid("non-text or oversized frame");
            return;
        }
        let json: unknown;
        try {
            json = JSON.parse(data);
        } catch {
            this.countInvalid("malformed JSON");
            return;
        }
        const parsed = this.options.schema.safeParse(json);
        if (!parsed.success) {
            this.countInvalid(parsed.error.issues[0]?.message ?? "schema mismatch");
            return;
        }
        this.stats_ = { ...this.stats_, received: this.stats_.received + 1 };
        this.options.onMessage(parsed.data);
    }

    private countInvalid(reason: string): void {
        this.stats_ = { ...this.stats_, invalid: this.stats_.invalid + 1 };
        console.warn(`[ws] dropped invalid frame: ${reason}`);
    }

    private scheduleReconnect(): void {
        if (this.stopped) return;
        this.attempt += 1;
        const { baseMs, maxMs } = this.options.backoff ?? DEFAULT_BACKOFF;
        const retryInMs = backoffDelay(
            this.attempt,
            baseMs,
            maxMs,
            (this.options.random ?? Math.random)(),
        );
        this.stats_ = { ...this.stats_, reconnects: this.stats_.reconnects + 1 };
        this.setState({ status: "reconnecting", attempt: this.attempt, retryInMs });
        this.retryTimer = setTimeout(() => {
            this.retryTimer = null;
            this.connect();
        }, retryInMs);
    }

    private setState(state: ConnectionState): void {
        this.state_ = state;
        this.options.onStateChange?.(state);
    }
}
