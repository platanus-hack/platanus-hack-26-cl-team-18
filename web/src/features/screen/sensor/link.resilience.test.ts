/**
 * ScreenSensor decides what runs from the backend's `live.status`: these are the
 * demo-day ways that could go wrong (a status for another screen, standby that
 * leaves the camera on, a mic left billing an empty room, a reconnect that never
 * re-announces the agent). Socket, camera and mic are fakes.
 */
import type { LiveStatus } from "@/contract";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SnapshotFacts } from "./vision";

const fakes = vi.hoisted(() => ({
    socket: null as null | {
        options: {
            onMessage: (m: unknown) => void;
            onStateChange: (s: { status: string }) => void;
        };
        sent: { type: string; role?: string; seq: number }[];
        start: ReturnType<typeof vi.fn>;
        stop: ReturnType<typeof vi.fn>;
    },
    vision: null as null | {
        onSnapshot: (facts: SnapshotFacts) => void;
        start: ReturnType<typeof vi.fn>;
        stop: ReturnType<typeof vi.fn>;
    },
    speech: null as null | {
        start: ReturnType<typeof vi.fn>;
        stop: ReturnType<typeof vi.fn>;
        retryNow: ReturnType<typeof vi.fn>;
    },
    /** The browser's permission status objects, by name ("camera", "microphone"). */
    permissions: new Map<string, { state: string; onchange: (() => void) | null }>(),
}));

vi.mock("@/lib/ws/typed-socket", () => ({
    TypedSocket: class {
        sent: { type: string }[] = [];
        start = vi.fn();
        stop = vi.fn();
        constructor(readonly options: unknown) {
            fakes.socket = this as unknown as typeof fakes.socket;
        }
        send(message: { type: string }) {
            this.sent.push(message);
        }
        sendWithBytes(message: { type: string }) {
            this.sent.push(message);
        }
    },
}));
vi.mock("./vision", () => ({
    BrowserVision: class {
        start = vi.fn(() => Promise.resolve());
        stop = vi.fn();
        constructor(readonly onSnapshot: (facts: SnapshotFacts) => void) {
            fakes.vision = this;
        }
    },
}));
vi.mock("./speech", () => ({
    BrowserSpeech: class {
        start = vi.fn(() => Promise.resolve());
        stop = vi.fn();
        retryNow = vi.fn();
        constructor() {
            fakes.speech = this;
        }
    },
}));

const { ScreenSensor, SPEECH_LINGER_MS, sensingEnabled } = await import("./link");

function status(overrides: Partial<LiveStatus> = {}): LiveStatus {
    return {
        v: 1,
        type: "live.status",
        screen_id: "screen-1",
        sensing: true,
        listening: false,
        ...overrides,
    } as LiveStatus;
}

function snapshot(people: number): SnapshotFacts {
    return { people } as SnapshotFacts;
}

const socket = () => fakes.socket!;
const vision = () => fakes.vision!;
const speech = () => fakes.speech!;

let current: InstanceType<typeof ScreenSensor>;

beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    fakes.permissions.clear();
    Object.defineProperty(navigator, "permissions", {
        configurable: true,
        value: {
            query: ({ name }: { name: string }) => {
                const status = { state: "prompt", onchange: null as (() => void) | null };
                fakes.permissions.set(name, status);
                return Promise.resolve(status);
            },
        },
    });
    current = new ScreenSensor("screen-1", "token");
    current.start();
});

afterEach(() => {
    current.stop();
    vi.useRealTimers();
    Reflect.deleteProperty(navigator, "permissions");
});

/** The browser flips a permission in site settings (or answers the prompt). */
async function permission(name: "camera" | "microphone", state: string) {
    await vi.waitFor(() => {
        expect(fakes.permissions.get(name)).toBeDefined();
    });
    const status = fakes.permissions.get(name)!;
    status.state = state;
    status.onchange?.();
}

describe("ScreenSensor on a demo day", () => {
    it("a live.status for another screen on the same backend changes nothing here", () => {
        socket().options.onMessage(status({ screen_id: "screen-2" }));
        expect(vision().start).not.toHaveBeenCalled();
        expect(vision().stop).not.toHaveBeenCalled();
    });

    it("standby closes the camera, not just the uplink", () => {
        socket().options.onMessage(status({ sensing: true }));
        expect(vision().start).toHaveBeenCalledTimes(1);
        socket().options.onMessage(status({ sensing: false }));
        expect(vision().stop).toHaveBeenCalled();
    });

    it("every repeated live.status re-arms a camera that recovered by stopping itself", () => {
        for (let i = 0; i < 3; i++) socket().options.onMessage(status({ sensing: true }));
        expect(vision().start).toHaveBeenCalledTimes(3); // start() is idempotent; recovery relies on this
    });

    it("listening in an empty room keeps the mic shut (Scribe bills session time)", () => {
        socket().options.onMessage(status({ listening: true }));
        vision().onSnapshot(snapshot(0));
        expect(speech().start).not.toHaveBeenCalled();
    });

    it("someone arriving opens the mic; 20 s after the last person it closes", () => {
        socket().options.onMessage(status({ listening: true }));
        vision().onSnapshot(snapshot(2));
        expect(speech().start).toHaveBeenCalled();
        speech().stop.mockClear(); // the empty-room stop from the first live.status

        vi.advanceTimersByTime(SPEECH_LINGER_MS - 1_000);
        vision().onSnapshot(snapshot(0));
        expect(speech().stop).not.toHaveBeenCalled(); // a brief miss is not a leave

        vi.advanceTimersByTime(2_000);
        vision().onSnapshot(snapshot(0));
        expect(speech().stop).toHaveBeenCalled();
    });

    it("speech mode switched off mid-conversation closes the mic at once", () => {
        socket().options.onMessage(status({ listening: true }));
        vision().onSnapshot(snapshot(1));
        speech().stop.mockClear();
        socket().options.onMessage(status({ listening: false }));
        expect(speech().stop).toHaveBeenCalled();
    });

    it("a reconnect re-announces both roles, so the backend does not treat the agent as gone", () => {
        socket().options.onStateChange({ status: "open" });
        socket().options.onStateChange({ status: "closed" });
        socket().options.onStateChange({ status: "connecting" });
        socket().options.onStateChange({ status: "open" });
        const hellos = socket().sent.filter((m) => m.type === "agent.hello");
        expect(hellos.map((m) => m.role)).toEqual(["vision", "speech", "vision", "speech"]);
        const seqs = hellos.map((m) => m.seq);
        expect(new Set(seqs).size).toBe(seqs.length); // never a duplicate seq (dedupe key)
    });

    it("snapshot seq keeps counting across a reconnect (the backend dedupes on it)", () => {
        vision().onSnapshot(snapshot(1));
        socket().options.onStateChange({ status: "closed" });
        socket().options.onStateChange({ status: "open" });
        vision().onSnapshot(snapshot(1));
        const seqs = socket()
            .sent.filter((m) => m.type === "audience.snapshot")
            .map((m) => m.seq);
        expect(seqs).toEqual([1, 2]);
    });
});

describe("ScreenSensor when camera or mic access changes", () => {
    it("camera allowed again while live reopens it at once (no wait for the next status)", async () => {
        socket().options.onMessage(status({ sensing: true }));
        vision().start.mockClear();
        await permission("camera", "granted");
        expect(vision().start).toHaveBeenCalledTimes(1);
    });

    it("camera allowed during standby stays closed", async () => {
        socket().options.onMessage(status({ sensing: false }));
        await permission("camera", "granted");
        expect(vision().start).not.toHaveBeenCalled();
    });

    it("camera access revoked closes it (the next status asks again)", async () => {
        socket().options.onMessage(status({ sensing: true }));
        vision().stop.mockClear();
        await permission("camera", "denied");
        expect(vision().stop).toHaveBeenCalled();
    });

    it("mic allowed again skips the backoff and opens if someone is there", async () => {
        socket().options.onMessage(status({ listening: true }));
        vision().onSnapshot(snapshot(1));
        speech().start.mockClear();
        await permission("microphone", "granted");
        expect(speech().retryNow).toHaveBeenCalled();
        expect(speech().start).toHaveBeenCalled();
    });

    it("mic access revoked closes Scribe", async () => {
        socket().options.onMessage(status({ listening: true }));
        vision().onSnapshot(snapshot(1));
        speech().stop.mockClear();
        await permission("microphone", "denied");
        expect(speech().stop).toHaveBeenCalled();
    });

    it("a stopped sensor ignores later permission changes", async () => {
        socket().options.onMessage(status({ sensing: true }));
        current.stop();
        vision().start.mockClear();
        await permission("camera", "granted");
        expect(vision().start).not.toHaveBeenCalled();
    });

    it("a browser without the Permissions API still senses", () => {
        Reflect.deleteProperty(navigator, "permissions");
        const sensor = new ScreenSensor("screen-1", "token");
        expect(() => {
            sensor.start();
        }).not.toThrow();
        sensor.stop();
    });
});

describe("sensingEnabled", () => {
    it.each([
        ["", true],
        ["?sense=1", true],
        ["?sense=0", false],
        ["?demo=x&sense=0", false],
    ])("%s → %s", (search, expected) => {
        expect(sensingEnabled(search)).toBe(expected);
    });
});
