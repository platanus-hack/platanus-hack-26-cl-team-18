/**
 * The /screen page as its own perception agent (ADR 0010): one `/ws/agent` socket
 * (SCREEN_TOKEN as a subprotocol), hello as `vision` and `speech`, and the
 * backend's `live.status` decides what runs: the camera while `sensing`, the mic
 * while `listening`. Standby closes both devices, not just the uplink.
 *
 * Off with `/screen?sense=0` (the local station, where the edge agent senses).
 */
import {
    AgentOutboundSchema,
    type AgentInbound,
    type AgentOutbound,
    type LiveStatus,
} from "@/contract";
import { backendWsUrl } from "@/lib/config";
import { screenProtocols } from "@/lib/device";
import { TypedSocket } from "@/lib/ws/typed-socket";
import { BrowserSpeech } from "./speech";
import { BrowserVision } from "./vision";

export function sensingEnabled(search: string = window.location.search): boolean {
    return new URLSearchParams(search).get("sense") !== "0";
}

/** The mic stays open this long after the camera last saw someone. */
export const SPEECH_LINGER_MS = 20_000;

/**
 * Speech runs only while the backend says `listening` *and* someone is in front of
 * the screen: Scribe bills session time, and an empty room has nothing to hear.
 */
export function speechWanted(listening: boolean, lastPeopleAtMs: number | null, nowMs: number) {
    return listening && lastPeopleAtMs !== null && nowMs - lastPeopleAtMs <= SPEECH_LINGER_MS;
}

export class ScreenSensor {
    private readonly socket: TypedSocket<AgentOutbound, AgentInbound>;
    private readonly vision: BrowserVision;
    private readonly speech: BrowserSpeech;
    private seq = { snapshot: 0, utterance: 0, hello: 0, perception: 0, keyframe: 0 };
    private open = false;
    private listening = false;
    private lastPeopleAtMs: number | null = null;

    constructor(
        private readonly screenId: string,
        token: string | null,
    ) {
        this.socket = new TypedSocket<AgentOutbound, AgentInbound>({
            url: `${backendWsUrl}/ws/agent`,
            protocols: screenProtocols(token),
            schema: AgentOutboundSchema,
            onMessage: (message) => {
                if (message.type === "live.status" && message.screen_id === screenId) {
                    this.apply(message);
                }
            },
            onStateChange: (state) => {
                const wasOpen = this.open;
                this.open = state.status === "open";
                if (this.open && !wasOpen) this.hello();
            },
        });
        this.vision = new BrowserVision(
            (facts) => {
                if (facts.people > 0) this.lastPeopleAtMs = Date.now();
                this.updateSpeech();
                this.socket.send({
                    v: 1,
                    type: "audience.snapshot",
                    screen_id: screenId,
                    seq: ++this.seq.snapshot,
                    ...facts,
                });
            },
            (facts) => {
                this.socket.send({
                    v: 1,
                    type: "dev.perception",
                    screen_id: screenId,
                    seq: ++this.seq.perception,
                    ...facts,
                });
            },
            (facts, jpeg) => {
                this.socket.sendWithBytes(
                    {
                        v: 1,
                        type: "audience.keyframe",
                        screen_id: screenId,
                        seq: ++this.seq.keyframe,
                        content_type: "image/jpeg",
                        byte_length: jpeg.byteLength,
                        session_id: null,
                        track_id: null,
                        video_time_s: null,
                        redaction: "allowlist",
                        ...facts,
                    },
                    jpeg,
                );
            },
        );
        this.speech = new BrowserSpeech(screenId, token, (facts) => {
            this.socket.send({
                v: 1,
                type: "audience.utterance",
                screen_id: screenId,
                seq: ++this.seq.utterance,
                ...facts,
            });
        });
    }

    start(): void {
        this.socket.start();
    }

    stop(): void {
        this.socket.stop();
        this.vision.stop();
        this.speech.stop();
    }

    private hello(): void {
        for (const role of ["vision", "speech"] as const) {
            this.socket.send({
                v: 1,
                type: "agent.hello",
                screen_id: this.screenId,
                role,
                seq: ++this.seq.hello,
            });
        }
    }

    private apply(status: LiveStatus): void {
        if (status.sensing) void this.vision.start();
        else this.vision.stop();
        this.listening = status.listening;
        this.updateSpeech();
    }

    private updateSpeech(): void {
        if (speechWanted(this.listening, this.lastPeopleAtMs, Date.now())) void this.speech.start();
        else this.speech.stop();
    }
}
