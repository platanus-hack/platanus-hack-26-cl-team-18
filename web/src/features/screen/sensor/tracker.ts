/**
 * Anonymous, session-local tracklets (privacy invariant 3): greedy IoU matching
 * frame to frame, so dwell survives between detections. Ids are a counter that
 * resets with the page; nothing about a person is kept besides a box.
 * Pure: the caller passes the time.
 */
import type { Box } from "./pose";

/** A track not seen for this long is gone (a missed frame or two is not a leave). */
const LOST_MS = 1_200;
const MIN_IOU = 0.3;

export interface Track {
    readonly id: string;
    readonly box: Box;
    readonly firstSeenMs: number;
    readonly lastSeenMs: number;
    /** Since when the person has been looking continuously; null when not looking. */
    readonly lookingSinceMs: number | null;
}

export function iou(a: Box, b: Box): number {
    const w = Math.max(0, Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1));
    const h = Math.max(0, Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1));
    const inter = w * h;
    const area = (box: Box) => Math.max(0, box.x2 - box.x1) * Math.max(0, box.y2 - box.y1);
    const union = area(a) + area(b) - inter;
    return union > 0 ? inter / union : 0;
}

export class Tracker {
    private tracks: Track[] = [];
    private next = 1;

    /** Assign each detection a track; returns the tracks in detection order. */
    update(detections: readonly { box: Box; looking: boolean }[], nowMs: number): Track[] {
        const alive = this.tracks.filter((t) => nowMs - t.lastSeenMs <= LOST_MS);
        const free = new Set(alive.map((_, i) => i));
        const result: Track[] = [];
        for (const detection of detections) {
            let best = -1;
            let bestIou = MIN_IOU;
            for (const i of free) {
                const track = alive[i];
                if (track === undefined) continue;
                const overlap = iou(track.box, detection.box);
                if (overlap >= bestIou) {
                    best = i;
                    bestIou = overlap;
                }
            }
            const previous = best >= 0 ? alive[best] : undefined;
            if (best >= 0) free.delete(best);
            const track: Track = {
                id: previous?.id ?? `b${this.next++}`,
                box: detection.box,
                firstSeenMs: previous?.firstSeenMs ?? nowMs,
                lastSeenMs: nowMs,
                lookingSinceMs: detection.looking ? (previous?.lookingSinceMs ?? nowMs) : null,
            };
            result.push(track);
        }
        const unmatched = [...free].flatMap((i) => (alive[i] === undefined ? [] : [alive[i]]));
        this.tracks = [...result, ...unmatched];
        return result;
    }

    reset(): void {
        this.tracks = [];
    }
}
