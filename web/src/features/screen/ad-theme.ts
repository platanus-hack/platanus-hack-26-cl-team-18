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

/** Rolling-circle ratios that close into even, open figures (petals = denominator). */
const RATIOS: readonly (readonly [number, number])[] = [
    [5, 12],
    [7, 16],
    [3, 8],
    [7, 18],
    [5, 14],
];

/**
 * A stroke-only hypotrochoid (spirograph) as SVG path data in a −1..1 box: the
 * signature line figure of the ad system. Pure and seeded by the basket, so a
 * campaign keeps its figure across plays and every brand pair gets its own.
 */
export function spirographPath(seed: string, points = 1400): string {
    const [num, den] = RATIOS[Math.floor(unit(seed, 1) * RATIOS.length)] ?? [5, 12];
    const R = 1;
    const r = num / den;
    const d = 0.55 + unit(seed, 2) * 0.35;
    const turns = num * 2 * Math.PI;
    const scale = 1 / (R - r + d);
    const out: string[] = [];
    for (let i = 0; i <= points; i++) {
        const t = (i / points) * turns;
        const x = ((R - r) * Math.cos(t) + d * Math.cos(((R - r) / r) * t)) * scale;
        const y = ((R - r) * Math.sin(t) - d * Math.sin(((R - r) / r) * t)) * scale;
        out.push(`${i === 0 ? "M" : "L"}${x.toFixed(4)} ${y.toFixed(4)}`);
    }
    return out.join("");
}
