import { describe, expect, it } from "vitest";
import { SPEECH_LINGER_MS, speechWanted } from "./link";
import { decodePeople, distanceBand, isLooking, KP, letterbox, ROW, type Person } from "./pose";
import { toPcm16 } from "./speech";
import { Tracker } from "./tracker";

function row(score: number, cls: number, box: [number, number, number, number], kps: number[][]) {
    const out = new Float32Array(ROW);
    out.set(box, 0);
    out[4] = score;
    out[5] = cls;
    kps.forEach(([x, y, c], i) => {
        out.set([x ?? 0, y ?? 0, c ?? 0], 6 + i * 3);
    });
    return out;
}

function person(
    points: Partial<Record<number, [number, number]>>,
    box = { x1: 0, y1: 0, x2: 100, y2: 300 },
): Person {
    const keypoints = Array.from({ length: 17 }, (_, i) => {
        const p = points[i];
        return p ? { x: p[0], y: p[1], conf: 0.9 } : { x: 0, y: 0, conf: 0 };
    });
    return { box, score: 0.9, keypoints };
}

describe("decodePeople", () => {
    it("maps letterboxed rows back to frame pixels and drops low scores and non-persons", () => {
        const lb = letterbox(1280, 720); // scale 0.325, padY 91
        const rows = new Float32Array(ROW * 3);
        rows.set(row(0.9, 0, [0, 91, 416, 325], [[208, 208, 0.9]]), 0);
        rows.set(row(0.2, 0, [0, 0, 10, 10], []), ROW);
        rows.set(row(0.9, 3, [0, 0, 10, 10], []), ROW * 2);
        const people = decodePeople(rows, lb);
        expect(people).toHaveLength(1);
        expect(people[0]?.box.x2).toBeCloseTo(1280);
        expect(people[0]?.box.y1).toBeCloseTo(0);
        expect(people[0]?.box.y2).toBeCloseTo(720);
        expect(people[0]?.keypoints[0]?.x).toBeCloseTo(640);
    });
});

describe("geometry (mirrors edge/agent/vision/geometry.py)", () => {
    it("looks when the nose sits between the eyes", () => {
        expect(
            isLooking(
                person({ [KP.nose]: [50, 30], [KP.leftEye]: [60, 20], [KP.rightEye]: [40, 20] }),
            ),
        ).toBe(true);
        expect(
            isLooking(
                person({ [KP.nose]: [62, 30], [KP.leftEye]: [60, 20], [KP.rightEye]: [40, 20] }),
            ),
        ).toBe(false);
        expect(isLooking(person({ [KP.nose]: [50, 30] }))).toBe(false);
    });

    it("bands distance by shoulder width, else box height", () => {
        const shoulders = (w: number) =>
            person({ [KP.leftShoulder]: [w, 50], [KP.rightShoulder]: [0, 50] });
        expect(distanceBand(shoulders(300), 1280, 720)).toBe("near");
        expect(distanceBand(shoulders(100), 1280, 720)).toBe("far");
        expect(distanceBand(shoulders(200), 1280, 720)).toBe("mid");
        expect(distanceBand(person({}, { x1: 0, y1: 0, x2: 10, y2: 700 }), 1280, 720)).toBe("near");
    });
});

describe("Tracker", () => {
    it("keeps an id and dwell across frames and forgets lost tracks", () => {
        const tracker = new Tracker();
        const box = { x1: 0, y1: 0, x2: 100, y2: 100 };
        const [a] = tracker.update([{ box, looking: true }], 0);
        const [b] = tracker.update([{ box: { ...box, x1: 5, x2: 105 }, looking: true }], 500);
        expect(b?.id).toBe(a?.id);
        expect(b?.firstSeenMs).toBe(0);
        expect(b?.lookingSinceMs).toBe(0);
        const [c] = tracker.update([{ box, looking: false }], 5_000);
        expect(c?.id).not.toBe(a?.id);
    });
});

describe("toPcm16", () => {
    it("downsamples 48 kHz to 16 kHz and clips", () => {
        const pcm = toPcm16(new Float32Array(480).fill(2), 48_000);
        expect(pcm).toHaveLength(160);
        expect(pcm[0]).toBe(0x7fff);
    });
});

describe("speechWanted", () => {
    it("opens the mic only while listening and someone was seen recently", () => {
        expect(speechWanted(true, null, 0)).toBe(false);
        expect(speechWanted(false, 0, 0)).toBe(false);
        expect(speechWanted(true, 0, SPEECH_LINGER_MS)).toBe(true);
        expect(speechWanted(true, 0, SPEECH_LINGER_MS + 1)).toBe(false);
    });
});
