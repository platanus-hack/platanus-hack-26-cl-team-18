import { describe, expect, it } from "vitest";
import { clipBox, headBox } from "./keyframe";
import { MinorVote } from "./minor";
import type { Person } from "./pose";

function person(
    box: Person["box"],
    points: Partial<Record<number, readonly [number, number]>> = {},
    conf = 0.9,
): Person {
    const keypoints = Array.from({ length: 17 }, (_, i) => {
        const p = points[i];
        return p ? { x: p[0], y: p[1], conf } : { x: 0, y: 0, conf: 0 };
    });
    return { box, score: 0.9, keypoints };
}

// Ears 3/4, shoulders 5/6 (COCO-17).
const adultHead = { 3: [80, 40], 4: [120, 40], 5: [50, 90], 6: [150, 90] } as const; // 0.40
const childHead = { 3: [70, 40], 4: [130, 40], 5: [50, 90], 6: [150, 90] } as const; // 0.60

describe("MinorVote (port of edge minor.py)", () => {
    it("clears restricted categories only after enough clean adult frames", () => {
        const vote = new MinorVote();
        const adult = person({ x1: 0, y1: 20, x2: 200, y2: 700 }, adultHead);
        let reading = vote.update([adult], 720);
        expect(reading).toEqual({ value: false, confidence: 0.08, uncertain: true });
        for (let i = 0; i < 6; i++) reading = vote.update([adult], 720);
        expect(reading.value).toBe(false);
        expect(reading.uncertain).toBe(false);
    });

    it("stays uncertain without usable cues (one person, ears hidden)", () => {
        const vote = new MinorVote();
        const reading = vote.update([person({ x1: 0, y1: 20, x2: 200, y2: 700 })], 720);
        expect(reading.uncertain).toBe(true);
    });

    it("flags a child-sized person after the vote, never on one frame", () => {
        const vote = new MinorVote();
        const adult = person({ x1: 0, y1: 100, x2: 200, y2: 700 }, adultHead);
        const child = person({ x1: 300, y1: 340, x2: 450, y2: 700 }, childHead);
        expect(vote.update([adult, child], 720).value).toBe(false);
        let reading = vote.update([adult, child], 720);
        for (let i = 0; i < 5; i++) reading = vote.update([adult, child], 720);
        expect(reading.value).toBe(true);
        expect(reading.confidence).toBeGreaterThan(0);
    });

    it("resets when the room empties", () => {
        const vote = new MinorVote();
        expect(vote.update([], 720)).toEqual({ value: false, confidence: 0, uncertain: false });
    });
});

describe("keyframe geometry (port of edge head_box)", () => {
    it("pads the face keypoints inside the person box", () => {
        const p = person(
            { x1: 0, y1: 0, x2: 200, y2: 600 },
            { 0: [100, 60], 1: [110, 50], 2: [90, 50] },
        );
        const head = headBox(p);
        expect(head.x1).toBeLessThan(90);
        expect(head.x2).toBeGreaterThan(110);
        expect(head.y1).toBeGreaterThanOrEqual(0);
        expect(head.y2).toBeGreaterThan(60);
    });

    it("blacks the top of the box when the face is not visible", () => {
        expect(headBox(person({ x1: 0, y1: 100, x2: 200, y2: 600 }))).toEqual({
            x1: 0,
            y1: 100,
            x2: 200,
            y2: 240,
        });
    });

    it("clips boxes to whole frame pixels", () => {
        expect(clipBox({ x1: -5, y1: 10.4, x2: 1300, y2: 20.2 }, 1280, 720)).toEqual({
            x1: 0,
            y1: 10,
            x2: 1280,
            y2: 21,
        });
        expect(clipBox({ x1: 1300, y1: 0, x2: 1400, y2: 10 }, 1280, 720)).toBeNull();
    });
});
