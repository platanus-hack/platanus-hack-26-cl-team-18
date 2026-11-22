/**
 * The advertiser self-serve setup, as pure data: the steps, the bid strategies and
 * the exact decimal math the page shows. No React, no IO (unit-tested).
 */

export const STEPS = [
    { key: "brand", label: "Marca" },
    { key: "audience", label: "Audiencias y canastas" },
    { key: "budget", label: "Presupuesto" },
    { key: "bid", label: "Puja" },
    { key: "live", label: "Al aire" },
] as const;

export type Step = (typeof STEPS)[number]["key"];

export function stepIndex(step: Step): number {
    return STEPS.findIndex((s) => s.key === step);
}

export function nextStep(step: Step): Step {
    return STEPS[Math.min(stepIndex(step) + 1, STEPS.length - 1)]?.key ?? step;
}

export function previousStep(step: Step): Step {
    return STEPS[Math.max(stepIndex(step) - 1, 0)]?.key ?? step;
}

export function parseStep(raw: string | null): Step {
    return STEPS.find((s) => s.key === raw)?.key ?? "brand";
}

/** Bid strategies are named multipliers on the catalog rates (backend bounds: 0.2–5.0). */
export const STRATEGIES = [
    {
        key: "prudent",
        multiplier: "0.8",
        title: "Prudente",
        description: "Ganas los momentos baratos y cedes los disputados.",
    },
    {
        key: "standard",
        multiplier: "1",
        title: "Estándar",
        description: "Pujas la tarifa aprobada de tu campaña.",
    },
    {
        key: "competitive",
        multiplier: "1.5",
        title: "Competitiva",
        description: "Peleas los momentos que también quiere tu competencia.",
    },
    {
        key: "aggressive",
        multiplier: "2.5",
        title: "Agresiva",
        description: "Priorizas aparecer, aunque cada segundo cueste más.",
    },
] as const;

export type Strategy = (typeof STRATEGIES)[number];

/** The strategy whose multiplier equals the live one, or null for a custom slider value. */
export function strategyFor(multiplier: string): Strategy | null {
    const value = toUnits(multiplier);
    return STRATEGIES.find((s) => toUnits(s.multiplier) === value) ?? null;
}

const SCALE = 10_000n; // 4 decimals: the backend's MoneyRate precision

function toUnits(decimal: string): bigint {
    const [whole = "0", fraction = ""] = decimal.split(".");
    return BigInt(whole) * SCALE + BigInt(fraction.padEnd(4, "0").slice(0, 4));
}

function fromUnits(units: bigint): string {
    const whole = units / SCALE;
    const fraction = (units % SCALE).toString().padStart(4, "0").replace(/0+$/, "");
    return fraction ? `${whole.toString()}.${fraction}` : whole.toString();
}

/**
 * The most a campaign pays per attention-second at `multiplier`: exact decimal math on
 * strings (no float), rounded half-up to 2 decimals for display.
 */
export function maxRate(rate: string, multiplier: string): string {
    const product = (toUnits(rate) * toUnits(multiplier)) / SCALE;
    const cents = (product + 50n) / 100n; // half-up to 2 decimals
    return fromUnits(cents * 100n);
}

/** Top-up presets in whole CLP; the backend caps one top-up at CLP 10.000.000. */
export const TOP_UPS = [10_000, 50_000, 100_000] as const;
export const MAX_TOP_UP = 10_000_000;

/** A typed top-up ("25.000", "25000", "$25.000") as whole CLP, or null when invalid. */
export function parseTopUp(raw: string): number | null {
    const digits = raw.replace(/[$.\s]/g, "");
    if (!/^\d+$/.test(digits)) return null;
    const value = Number(digits);
    return value >= 1 && value <= MAX_TOP_UP ? value : null;
}
