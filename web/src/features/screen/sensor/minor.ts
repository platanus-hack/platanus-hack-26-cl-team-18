/**
 * A coarse "a child is probably here" flag from pose geometry: a port of
 * edge/agent/vision/minor.py (`MinorVote`, default `MinorConfig`). It may only
 * **block** age-restricted ads (invariant 2), never target anyone.
 *
 * Two body-size cues per person: stature against peers standing on the same
 * floor line, and ear-to-ear over shoulder width. A 7-of-12-frame vote keeps one
 * blip from flipping it. Nothing per person leaves; only the moment's flag.
 * Pure: no camera, no clock.
 */
import type { MinorLikely } from "@/contract";
import type { Person } from "./pose";

const LEFT_EAR = 3;
const RIGHT_EAR = 4;
const LEFT_SHOULDER = 5;
const RIGHT_SHOULDER = 6;

const C = {
    minKp: 0.5,
    edgeMargin: 0.02,
    groundTolerance: 0.06,
    heightAdult: 0.8,
    heightChild: 0.65,
    headAdult: 0.48,
    headChild: 0.58,
    minShoulderPx: 40,
    heightWeight: 0.6,
    singleCueDiscount: 0.75,
    personThreshold: 0.5,
    window: 12,
    minHits: 7,
    maxConfidence: 0.9,
} as const;

const ramp = (value: number, start: number, full: number) =>
    Math.min(1, Math.max(0, (value - start) / (full - start)));

const height = (p: Person) => p.box.y2 - p.box.y1;

function uncut(p: Person, frameHeight: number): boolean {
    const margin = frameHeight * C.edgeMargin;
    return p.box.y1 > margin && p.box.y2 < frameHeight - margin;
}

export function statureCue(p: Person, peers: readonly Person[], frameHeight: number) {
    if (frameHeight <= 0 || !uncut(p, frameHeight)) return null;
    const tolerance = frameHeight * C.groundTolerance;
    const heights = peers
        .filter(
            (peer) =>
                peer !== p &&
                uncut(peer, frameHeight) &&
                Math.abs(peer.box.y2 - p.box.y2) <= tolerance,
        )
        .map(height);
    const tallest = Math.max(0, ...heights);
    if (heights.length === 0 || tallest <= 0) return null;
    return ramp(height(p) / tallest, C.heightAdult, C.heightChild);
}

export function headCue(p: Person) {
    const points = [LEFT_EAR, RIGHT_EAR, LEFT_SHOULDER, RIGHT_SHOULDER].map((i) => p.keypoints[i]);
    const [le, re, ls, rs] = points;
    if (le === undefined || re === undefined || ls === undefined || rs === undefined) return null;
    if (points.some((q) => q === undefined || q.conf < C.minKp)) return null;
    const shoulders = Math.abs(ls.x - rs.x);
    if (shoulders < C.minShoulderPx) return null;
    return ramp(Math.abs(le.x - re.x) / shoulders, C.headAdult, C.headChild);
}

export function childEvidence(p: Person, peers: readonly Person[], frameHeight: number) {
    const stature = statureCue(p, peers, frameHeight);
    const head = headCue(p);
    if (stature !== null && head !== null) {
        return C.heightWeight * stature + (1 - C.heightWeight) * head;
    }
    const single = stature ?? head;
    return single === null ? null : single * C.singleCueDiscount;
}

interface FrameVote {
    readonly evaluated: boolean;
    readonly hit: boolean;
    readonly strength: number;
}

const round2 = (x: number) => Math.round(x * 100) / 100;

export class MinorVote {
    private frames: FrameVote[] = [];
    private uncertain = false;

    update(people: readonly Person[], frameHeight: number): MinorLikely {
        if (people.length === 0) {
            this.reset();
            return { value: false, confidence: 0, uncertain: false };
        }
        const cues = people.map((p) => childEvidence(p, people, frameHeight));
        this.uncertain = cues.some((e) => e === null);
        const evidence = cues.filter((e): e is number => e !== null);
        const hits = evidence.filter((e) => e >= C.personThreshold);
        this.frames.push({
            evaluated: evidence.length > 0,
            hit: hits.length > 0,
            strength: Math.max(0, ...hits),
        });
        if (this.frames.length > C.window) this.frames.shift();
        return this.reading();
    }

    reading(): MinorLikely {
        const hitFrames = this.frames.filter((f) => f.hit);
        if (hitFrames.length >= C.minHits) {
            const mean = hitFrames.reduce((s, f) => s + f.strength, 0) / hitFrames.length;
            const share = hitFrames.length / C.window;
            return {
                value: true,
                confidence: round2(C.maxConfidence * share * mean),
                uncertain: false,
            };
        }
        const clean = this.frames.filter((f) => f.evaluated && !f.hit).length;
        return {
            value: false,
            confidence: round2((C.maxConfidence * clean) / C.window),
            uncertain: this.uncertain || clean < C.minHits,
        };
    }

    reset(): void {
        this.frames = [];
        this.uncertain = false;
    }
}
