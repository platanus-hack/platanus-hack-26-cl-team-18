export type AdTheme = "light" | "dark";

/** Store hours read as daylight: a light field under shop lighting, dark at night (less glare). */
export function themeForHour(hour: number): AdTheme {
    return hour >= 7 && hour < 20 ? "light" : "dark";
}

/** Deterministic hash → [0, 1): the same basket always draws the same figure. */
function unit(seed: string, salt: number): number {
    let h = 2166136261 ^ salt;
    for (let i = 0; i < seed.length; i++) h = Math.imul(h ^ seed.charCodeAt(i), 16777619);
    return ((h >>> 0) % 10_000) / 10_000;
}

/**
 * The ADverse signature figure, stroke-only, as SVG path data in a −1..1 box.
 * It is the mark's own construction drawn as lines: rays fanning down from a
 * clear ring around a focal point (the beams under the dot), and concentric
 * rings around that point (the moment's pulse). Pure and seeded by the basket,
 * so a campaign keeps its figure across plays and every brand pair gets its own.
 */
export function signaturePath(seed: string): string {
    const rays = 9 + Math.floor(unit(seed, 1) * 4) * 2; // 9, 11, 13 or 15
    const halfFan = ((40 + unit(seed, 2) * 30) * Math.PI) / 180; // 40°–70° each side
    const tilt = ((unit(seed, 3) - 0.5) * 24 * Math.PI) / 180; // ±12°
    const clear = 0.14 + unit(seed, 4) * 0.06; // the gap around the dot
    const rings = 3 + Math.floor(unit(seed, 5) * 3); // 3–5
    const step = 0.09 + unit(seed, 6) * 0.04;
    const f = (n: number) => n.toFixed(4);
    const out: string[] = [];
    for (let k = 0; k < rings; k++) {
        const r = Math.min(clear + k * step, 0.98);
        out.push(`M${f(r)} 0A${f(r)} ${f(r)} 0 1 1 ${f(-r)} 0A${f(r)} ${f(r)} 0 1 1 ${f(r)} 0`);
    }
    for (let i = 0; i < rays; i++) {
        const a = Math.PI / 2 + tilt - halfFan + (2 * halfFan * i) / (rays - 1);
        const [cx, cy] = [Math.cos(a), Math.sin(a)];
        out.push(`M${f(cx * clear)} ${f(cy * clear)}L${f(cx)} ${f(cy)}`);
    }
    return out.join("");
}
