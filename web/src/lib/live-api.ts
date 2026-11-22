import { LiveStatusSchema, type LiveSet, type LiveStatus } from "@/contract";
import { useCallback, useEffect, useRef, useState } from "react";
import { backendHttpUrl } from "./config";
import { demoHeaders } from "./demo";

/**
 * The console's "En vivo" switch (app/realtime/live_switch.py) over plain HTTP,
 * so it works from the second laptop (ADR 0008 D7). Every response is validated
 * against the generated contract.
 */

export async function fetchLive(screenId: string, signal?: AbortSignal): Promise<LiveStatus> {
    const response = await fetch(
        `${backendHttpUrl}/api/live?screen_id=${encodeURIComponent(screenId)}`,
        signal ? { signal } : {},
    );
    if (!response.ok) throw new Error(`live: HTTP ${response.status}`);
    return LiveStatusSchema.parse(await response.json());
}

export type LiveChange = Partial<Pick<LiveSet, "live" | "mic">>;

/**
 * Absolute values: a retried or double-clicked request is harmless (the last click wins).
 * Carries the demo key this browser remembered (if any): with DEMO_KEY set the backend
 * accepts the switch from the station itself, or with the key through the tunnel.
 */
export async function setLive(screenId: string, change: LiveChange): Promise<LiveStatus> {
    // null keeps that axis as it is: the master switch and the mic are independent.
    const message: LiveSet = {
        type: "live.set",
        v: 1,
        screen_id: screenId,
        live: change.live ?? null,
        mic: change.mic ?? null,
    };
    const response = await fetch(`${backendHttpUrl}/api/live`, {
        method: "POST",
        headers: { "content-type": "application/json", ...demoHeaders() },
        body: JSON.stringify(message),
    });
    if (!response.ok) throw new Error(`live: HTTP ${response.status}`);
    return LiveStatusSchema.parse(await response.json());
}

export interface LiveState {
    readonly status: LiveStatus | null;
    /** Polling failed: the backend is unreachable (the button is disabled). */
    readonly unreachable: boolean;
    /** The last change failed; the shown status is still the backend's truth. */
    readonly failed: string | null;
    readonly pending: boolean;
    readonly apply: (change: LiveChange) => void;
}

/** Poll the switch (agents connect and leave on their own) and apply changes. */
export function useLive(screenId: string, intervalMs = 1000): LiveState {
    const [status, setStatus] = useState<LiveStatus | null>(null);
    const [unreachable, setUnreachable] = useState(false);
    const [failed, setFailed] = useState<string | null>(null);
    const [pending, setPending] = useState(false);
    // A poll that started before a write must not overwrite the write's answer.
    const generation = useRef(0);

    useEffect(() => {
        const controller = new AbortController();
        const load = () => {
            const started = generation.current;
            fetchLive(screenId, controller.signal)
                .then((next) => {
                    if (controller.signal.aborted || started !== generation.current) return;
                    setStatus(next);
                    setUnreachable(false);
                })
                .catch(() => {
                    if (!controller.signal.aborted) setUnreachable(true);
                });
        };
        load();
        const timer = setInterval(load, intervalMs);
        return () => {
            controller.abort();
            clearInterval(timer);
        };
    }, [screenId, intervalMs]);

    const apply = useCallback(
        (change: LiveChange) => {
            generation.current += 1;
            setPending(true);
            setLive(screenId, change)
                .then((next) => {
                    setStatus(next);
                    setFailed(null);
                })
                .catch((reason: unknown) => {
                    setFailed(String(reason));
                })
                .finally(() => {
                    setPending(false);
                });
        },
        [screenId],
    );

    return { status, unreachable, failed, pending, apply };
}
