/**
 * The only frame that leaves the browser (docs/privacy.md G1, ADR 0010): an
 * allow-list keyframe, the port of edge `allowlist_keyframe` + `keyframe_regions`.
 * It starts from black, copies only the detected people, then blacks out every
 * head (opaque, not blur). The backend re-checks the pixels against the declared
 * `kept`/`heads` boxes and drops any mismatch before Claude sees it.
 */
import type { RedactionBox } from "@/contract";
import type { Box, Person } from "./pose";

export const KEYFRAME_LONG_SIDE = 512;
const QUALITY = 0.8;
const MAX_REGIONS = 64;

/** Port of edge `geometry.head_box`: face keypoints padded, else the top of the box. */
export function headBox(person: Person): Box {
    const box = person.box;
    const face = [0, 1, 2, 3, 4]
        .map((i) => person.keypoints[i])
        .filter((p): p is NonNullable<typeof p> => p !== undefined && p.conf >= 0.2);
    const width = box.x2 - box.x1;
    if (face.length >= 2) {
        const xs = face.map((p) => p.x);
        const ys = face.map((p) => p.y);
        const span = Math.max(Math.max(...xs) - Math.min(...xs), width * 0.25);
        const cx = (Math.max(...xs) + Math.min(...xs)) / 2;
        const cy = (Math.max(...ys) + Math.min(...ys)) / 2;
        const half = span * 0.9;
        const x1 = Math.max(box.x1, cx - half);
        const y1 = Math.max(box.y1, cy - half * 1.3);
        const x2 = Math.min(box.x2, cx + half);
        const y2 = Math.min(box.y2, cy + half * 1.1);
        if (x1 < x2 && y1 < y2) return { x1, y1, x2, y2 };
    }
    return { x1: box.x1, y1: box.y1, x2: box.x2, y2: box.y1 + (box.y2 - box.y1) * 0.28 };
}

/** Clip to the frame in whole pixels; null when nothing is left. */
export function clipBox(box: Box, width: number, height: number): Box | null {
    const x1 = Math.max(0, Math.floor(box.x1));
    const y1 = Math.max(0, Math.floor(box.y1));
    const x2 = Math.min(width, Math.ceil(box.x2));
    const y2 = Math.min(height, Math.ceil(box.y2));
    return x2 > x1 && y2 > y1 ? { x1, y1, x2, y2 } : null;
}

export interface Keyframe {
    readonly jpeg: ArrayBuffer;
    readonly kept: RedactionBox[];
    readonly heads: RedactionBox[];
}

export async function allowlistKeyframe(
    source: CanvasImageSource,
    width: number,
    height: number,
    people: readonly Person[],
): Promise<Keyframe | null> {
    const kept = people
        .map((p) => clipBox(p.box, width, height))
        .filter((b): b is Box => b !== null)
        .slice(0, MAX_REGIONS);
    if (kept.length === 0) return null;
    const heads = people
        .map((p) => clipBox(headBox(p), width, height))
        .filter((b): b is Box => b !== null)
        .slice(0, MAX_REGIONS);
    const scale = KEYFRAME_LONG_SIDE / Math.max(width, height);
    const w = Math.round(width * scale);
    const h = Math.round(height * scale);
    const canvas = new OffscreenCanvas(w, h);
    const ctx = canvas.getContext("2d");
    if (ctx === null) return null;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, w, h);
    for (const b of kept) {
        ctx.drawImage(
            source,
            b.x1,
            b.y1,
            b.x2 - b.x1,
            b.y2 - b.y1,
            b.x1 * scale,
            b.y1 * scale,
            (b.x2 - b.x1) * scale,
            (b.y2 - b.y1) * scale,
        );
    }
    ctx.fillStyle = "#000";
    for (const b of heads) {
        // Grow by a pixel so resampling never leaves a lit edge inside the head box.
        ctx.fillRect(
            b.x1 * scale - 1,
            b.y1 * scale - 1,
            (b.x2 - b.x1) * scale + 2,
            (b.y2 - b.y1) * scale + 2,
        );
    }
    const blob = await canvas.convertToBlob({ type: "image/jpeg", quality: QUALITY });
    const norm = (b: Box): RedactionBox => ({
        x1: b.x1 / width,
        y1: b.y1 / height,
        x2: b.x2 / width,
        y2: b.y2 / height,
    });
    return { jpeg: await blob.arrayBuffer(), kept: kept.map(norm), heads: heads.map(norm) };
}
