/**
 * What the camera's window does to people on stage: heads cut off by the top edge,
 * someone too close for their shoulders to fit, a person half out of the side,
 * backs turned, a passer-by who blinks out of view for a frame, a group crossing.
 * Pure functions (pose, tracker, minor vote): no camera, no model, no clock.
 */
import { describe, expect, it } from "vitest";
import { MinorVote } from "./minor";
import { decodePeople, distanceBand, isLooking, KP, letterbox, ROW, type Person } from "./pose";
import { Tracker } from "./tracker";

const W = 1280;
const H = 720;

type Kp = [x: number, y: number, conf?: number];

function person(
    box: [number, number, number, number],
    points: Partial<Record<number, Kp>> = {},
): Person {
    const keypoints = Array.from({ length: 17 }, (_, i) => {
        const p = points[i];
        return p ? { x: p[0], y: p[1], conf: p[2] ?? 0.9 } : { x: 0, y: 0, conf: 0 };
    });
    const [x1, y1, x2, y2] = box;
    return { box: { x1, y1, x2, y2 }, score: 0.9, keypoints };
}

/** Ears and shoulders of an adult-proportioned body centred at `cx` (head/shoulders ≈ 0.4). */
function adult(box: [number, number, number, number]): Person {
    const cx = (box[0] + box[2]) / 2;
    const top = box[1];
    return person(box, {
        3: [cx - 20, top + 40],
        4: [cx + 20, top + 40],
        [KP.leftShoulder]: [cx - 50, top + 90],
        [KP.rightShoulder]: [cx + 50, top + 90],
    });
}

function vote(frames: Person[][]) {
    const minor = new MinorVote();
    let reading = minor.update([], H);
    for (const people of frames) reading = minor.update(people, H);
    return reading;
}

const repeat = <T>(n: number, value: T): T[] => Array.from({ length: n }, () => value);

describe("minor vote at the edges of the frame (it may only block, but a false block kills the beer beat)", () => {
    it("a tall adult whose head is cut by the top edge does not make the adult beside them look like a child", () => {
        const cut = adult([200, 0, 400, 700]); // leaning in: the top of the box is the frame edge
        const shorter = adult([700, 250, 900, 700]); // 450 px vs the cut 700 px: 0.64 would read as a child
        const reading = vote(repeat(12, [cut, shorter]));
        expect(reading.value).toBe(false);
    });

    it("an adult too close for their feet to fit is not compared on stature", () => {
        const close = adult([100, 30, 600, H]); // feet below the frame
        const far = adult([800, 300, 900, 650]);
        expect(vote(repeat(12, [close, far])).value).toBe(false);
    });

    it("a shorter person further back (another floor line) is not read as a child", () => {
        const front = adult([200, 80, 450, 700]);
        const back = adult([800, 200, 900, 420]); // feet much higher in the image: further away
        expect(vote(repeat(12, [front, back])).value).toBe(false);
    });

    it("a child-shaped blip on alternate frames (6 of 12) never flips the flag", () => {
        const parent = adult([200, 100, 400, 700]);
        const blip = adult([700, 380, 820, 700]); // ~0.53 of the parent's height on the same floor
        const frames = Array.from({ length: 12 }, (_, i) =>
            i % 2 === 0 ? [parent, blip] : [parent],
        );
        expect(vote(frames).value).toBe(false);
    });

    it("a lone person with no usable cues stays uncertain, so restricted ads stay blocked", () => {
        const backTurned = person([400, 100, 600, 700]); // no ears, no shoulders
        const reading = vote(repeat(12, [backTurned]));
        expect(reading.value).toBe(false);
        expect(reading.uncertain).toBe(true);
    });
});

describe("pose geometry for partial bodies", () => {
    it("a turned back or a head out of frame is not looking, and never NaN", () => {
        expect(isLooking(person([0, 0, 100, 300]))).toBe(false);
        expect(
            isLooking(
                person([0, 0, 100, 300], { [KP.leftEye]: [40, 20], [KP.rightEye]: [60, 20] }),
            ),
        ).toBe(false);
    });

    it("a profile (both eyes on one pixel) is not looking instead of dividing by zero", () => {
        const profile = person([0, 0, 100, 300], {
            [KP.nose]: [50, 30],
            [KP.leftEye]: [45, 20],
            [KP.rightEye]: [45, 20],
        });
        expect(isLooking(profile)).toBe(false);
    });

    it("low-confidence face keypoints (motion blur, backlight) do not count as looking", () => {
        const blurred = person([0, 0, 100, 300], {
            [KP.nose]: [50, 30, 0.3],
            [KP.leftEye]: [40, 20, 0.3],
            [KP.rightEye]: [60, 20, 0.3],
        });
        expect(isLooking(blurred)).toBe(false);
    });

    it("someone so close their shoulders leave the frame is still 'near'", () => {
        const close = person([0, 0, W, H]); // shoulders out of view: box height fallback
        expect(distanceBand(close, W, H)).toBe("near");
    });

    it("a tiny figure at the back is 'far'", () => {
        expect(distanceBand(person([600, 300, 640, 400]), W, H)).toBe("far");
    });

    it("a zero-sized frame (camera not ready) bands without throwing", () => {
        expect(() => distanceBand(person([0, 0, 10, 10]), 0, 0)).not.toThrow();
    });

    it("a truncated model output never yields a half-read person", () => {
        const rows = new Float32Array(ROW + 10);
        rows.set([0, 0, 100, 100, 0.9, 0], 0);
        rows.set([0, 0, 100, 100, 0.9, 0], ROW); // the partial second row
        expect(decodePeople(rows, letterbox(W, H))).toHaveLength(1);
    });

    it("NaN scores from a corrupted run are dropped", () => {
        const rows = new Float32Array(ROW);
        rows.set([0, 0, 100, 100, Number.NaN, 0], 0);
        expect(decodePeople(rows, letterbox(W, H))).toHaveLength(0);
        // A lost GPU device can return all-NaN tensors: an empty room, not 300 ghosts.
        expect(
            decodePeople(new Float32Array(ROW * 300).fill(Number.NaN), letterbox(W, H)),
        ).toHaveLength(0);
    });

    it("a portrait or square camera letterboxes without stretching", () => {
        for (const [w, h] of [
            [720, 1280],
            [640, 640],
            [1920, 1080],
        ] as const) {
            const lb = letterbox(w, h);
            expect(w * lb.scale).toBeLessThanOrEqual(416 + 1e-6);
            expect(h * lb.scale).toBeLessThanOrEqual(416 + 1e-6);
            expect(lb.padX).toBeGreaterThanOrEqual(0);
            expect(lb.padY).toBeGreaterThanOrEqual(0);
        }
    });
});

describe("tracker at the window's edge", () => {
    const box = (x: number) => ({ x1: x, y1: 100, x2: x + 200, y2: 600 });
    const at = (x: number) => ({ box: box(x), looking: true });

    it("a person who blinks out of detection for a frame or two keeps their id and dwell", () => {
        const tracker = new Tracker();
        const [first] = tracker.update([at(300)], 0);
        tracker.update([], 200);
        tracker.update([], 400);
        const [again] = tracker.update([at(310)], 600);
        expect(again?.id).toBe(first?.id);
        expect(again?.firstSeenMs).toBe(0);
    });

    it("someone who left the window for longer comes back as a new anonymous track", () => {
        const tracker = new Tracker();
        const [first] = tracker.update([at(300)], 0);
        tracker.update([], 1_000);
        const [back] = tracker.update([at(300)], 2_500);
        expect(back?.id).not.toBe(first?.id);
        expect(back?.firstSeenMs).toBe(2_500);
    });

    it("walking across the frame at normal pace keeps one id", () => {
        const tracker = new Tracker();
        const ids = new Set<string | undefined>();
        for (let i = 0; i < 20; i++) ids.add(tracker.update([at(i * 40)], i * 200)[0]?.id);
        expect(ids.size).toBe(1);
    });

    it("a group in one frame never shares an id", () => {
        const tracker = new Tracker();
        tracker.update([at(0), at(250), at(500)], 0);
        const ids = tracker.update([at(5), at(255), at(505), at(900)], 200).map((t) => t.id);
        expect(new Set(ids).size).toBe(4);
    });

    it("two overlapping detections of one person (a duplicate box) do not steal each other's track", () => {
        const tracker = new Tracker();
        const [first] = tracker.update([at(300)], 0);
        const tracks = tracker.update([at(300), at(305)], 200);
        expect(tracks[0]?.id).toBe(first?.id);
        expect(tracks[1]?.id).not.toBe(first?.id);
    });

    it("looking resets when they look away, so attention is not credited for a turned back", () => {
        const tracker = new Tracker();
        tracker.update([at(300)], 0);
        const [away] = tracker.update([{ box: box(300), looking: false }], 200);
        expect(away?.lookingSinceMs).toBeNull();
        const [back] = tracker.update([at(300)], 400);
        expect(back?.lookingSinceMs).toBe(400);
    });

    it("a clock that steps backwards (NTP correction) does not crash or drop the track", () => {
        const tracker = new Tracker();
        const [first] = tracker.update([at(300)], 10_000);
        const [after] = tracker.update([at(300)], 9_500);
        expect(after?.id).toBe(first?.id);
    });

    it("reset (standby) forgets everyone: no id survives into the next session", () => {
        const tracker = new Tracker();
        const [first] = tracker.update([at(300)], 0);
        tracker.reset();
        const [next] = tracker.update([at(300)], 100);
        expect(next?.id).not.toBe(first?.id);
    });
});
