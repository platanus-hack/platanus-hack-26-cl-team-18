import { describe, expect, it } from "vitest";
import { signaturePath } from "./ad-theme";

describe("signaturePath", () => {
    it("is deterministic per basket and differs between baskets", () => {
        expect(signaturePath("voltio+patio")).toBe(signaturePath("voltio+patio"));
        expect(signaturePath("voltio+patio")).not.toBe(signaturePath("rayo+patio"));
    });

    it("stays inside the unit box", () => {
        for (const seed of ["rayo+patio", "voltio+patio", "cafe+pastel", "x"]) {
            const d = signaturePath(seed);
            const points = [...d.matchAll(/[ML](-?[\d.]+) (-?[\d.]+)/g)].map(
                (m): [number, number] => [Number(m[1]), Number(m[2])],
            );
            const radii = [...d.matchAll(/A([\d.]+)/g)].map((m) => Number(m[1]));
            expect(points.length).toBeGreaterThan(0);
            expect(radii.length).toBeGreaterThan(0);
            expect(points.every(([x, y]) => Math.hypot(x, y) <= 1.0001)).toBe(true);
            expect(radii.every((r) => r <= 1)).toBe(true);
        }
    });
});
