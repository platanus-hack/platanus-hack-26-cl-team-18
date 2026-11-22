/**
 * YOLO26-pose output → people, the same geometry as the edge agent
 * (edge/agent/vision/geometry.py): looking from the nose between the eyes,
 * distance from shoulder width. Pure: no camera, no model, no clock.
 */
import type { DistanceBand } from "@/contract";

export const MODEL_SIZE = 416;
/** End-to-end head: [1, 300, 57] = x1 y1 x2 y2 score cls + 17 × (x y conf). */
export const ROW = 57;
const KP_OFFSET = 6;

export const KP = {
    nose: 0,
    leftEye: 1,
    rightEye: 2,
    leftShoulder: 5,
    rightShoulder: 6,
} as const;

export interface Box {
    readonly x1: number;
    readonly y1: number;
    readonly x2: number;
    readonly y2: number;
}

export interface Point {
    readonly x: number;
    readonly y: number;
    readonly conf: number;
}

export interface Person {
    readonly box: Box;
    readonly score: number;
    readonly keypoints: readonly Point[];
}

/** How a frame was squeezed into the model's square input (letterbox). */
export interface Letterbox {
    readonly scale: number;
    readonly padX: number;
    readonly padY: number;
}

export function letterbox(width: number, height: number, size = MODEL_SIZE): Letterbox {
    const scale = Math.min(size / width, size / height);
    return {
        scale,
        padX: (size - width * scale) / 2,
        padY: (size - height * scale) / 2,
    };
}

/** Decode the model rows into frame-pixel people above `minScore`. */
export function decodePeople(output: Float32Array, lb: Letterbox, minScore = 0.4): Person[] {
    const people: Person[] = [];
    const fx = (x: number) => (x - lb.padX) / lb.scale;
    const fy = (y: number) => (y - lb.padY) / lb.scale;
    for (let offset = 0; offset + ROW <= output.length; offset += ROW) {
        const score = output[offset + 4] ?? 0;
        if (score < minScore) continue;
        if ((output[offset + 5] ?? 0) !== 0) continue; // class 0: person
        const keypoints: Point[] = [];
        for (let k = 0; k < 17; k++) {
            const base = offset + KP_OFFSET + k * 3;
            keypoints.push({
                x: fx(output[base] ?? 0),
                y: fy(output[base + 1] ?? 0),
                conf: output[base + 2] ?? 0,
            });
        }
        people.push({
            box: {
                x1: fx(output[offset] ?? 0),
                y1: fy(output[offset + 1] ?? 0),
                x2: fx(output[offset + 2] ?? 0),
                y2: fy(output[offset + 3] ?? 0),
            },
            score,
            keypoints,
        });
    }
    return people;
}

const MIN_KP = 0.5;
const MAX_NOSE_OFFSET = 0.35;
const NEAR_SHOULDER = 0.22;
const FAR_SHOULDER = 0.1;
const NEAR_HEIGHT = 0.85;
const FAR_HEIGHT = 0.4;

function kp(person: Person, index: number): Point | null {
    const point = person.keypoints[index];
    return point !== undefined && point.conf >= MIN_KP ? point : null;
}

export function isLooking(person: Person): boolean {
    const nose = kp(person, KP.nose);
    const left = kp(person, KP.leftEye);
    const right = kp(person, KP.rightEye);
    if (nose === null || left === null || right === null) return false;
    const eyes = Math.abs(left.x - right.x);
    if (eyes <= 1e-6) return false;
    return Math.abs(nose.x - (left.x + right.x) / 2) / eyes <= MAX_NOSE_OFFSET;
}

export function distanceBand(person: Person, width: number, height: number): DistanceBand {
    const left = kp(person, KP.leftShoulder);
    const right = kp(person, KP.rightShoulder);
    if (left !== null && right !== null && width > 0) {
        const fraction = Math.abs(left.x - right.x) / width;
        if (fraction >= NEAR_SHOULDER) return "near";
        if (fraction <= FAR_SHOULDER) return "far";
        return "mid";
    }
    const fraction = height > 0 ? (person.box.y2 - person.box.y1) / height : 0;
    if (fraction >= NEAR_HEIGHT) return "near";
    if (fraction <= FAR_HEIGHT) return "far";
    return "mid";
}
