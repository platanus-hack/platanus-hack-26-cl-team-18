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
import { HEARTBEAT_IDLE_MS } from "@/lib/ws/heartbeat";
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
    private sensing = false;
    private stopped = false;
    private lastPeopleAtMs: number | null = null;
    private readonly watched: PermissionStatus[] = [];

    constructor(
        private readonly screenId: string,
        token: string | null,
    ) {
        this.socket = new TypedSocket<AgentOutbound, AgentInbound>({
            url: `${backendWsUrl}/ws/agent`,
            protocols: screenProtocols(token),
            // The backend repeats live.status to hello'd agents: silence means a dead uplink.
            idleTimeoutMs: HEARTBEAT_IDLE_MS,
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
        this.stopped = false;
        this.socket.start();
        this.watchPermission("camera", (granted) => {
            if (!granted) this.vision.stop();
            else if (this.sensing) void this.vision.start();
        });
        this.watchPermission("microphone", (granted) => {
            if (!granted) {
                this.speech.stop();
                return;
            }
            this.speech.retryNow();
            this.updateSpeech();
        });
    }

    stop(): void {
        this.stopped = true;
        for (const status of this.watched.splice(0)) status.onchange = null;
        this.socket.stop();
        this.vision.stop();
        this.speech.stop();
    }

    /**
     * Access granted or revoked in the browser (site settings, the prompt): act now
     * instead of on the next status or after the failure backoff. Revoking ends the
     * tracks too, but not in every browser. No Permissions API, or no "camera" name
     * (Firefox): the 5 s live.status and the speech backoff still recover.
     */
    private watchPermission(name: "camera" | "microphone", onChange: (granted: boolean) => void) {
        const permissions = (navigator as Partial<Navigator>).permissions;
        if (permissions === undefined) return;
        permissions
            .query({ name })
            .then((status) => {
                if (this.stopped) return;
                this.watched.push(status);
                status.onchange = () => {
                    if (!this.stopped) onChange(status.state === "granted");
                };
            })
            .catch(() => {});
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
        this.sensing = status.sensing;
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
