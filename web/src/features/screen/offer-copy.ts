import type { CreativeOffer } from "@/contract";

/** Chilean display digits: 8490 → "8.490" (no decimals for CLP). */
export function priceDigits(offer: CreativeOffer): string {
    return Number(offer.price.amount).toLocaleString("es-CL", { maximumFractionDigits: 0 });
}

const PRICE = /\$\s?\d[\d.,]*/g;
const TRAILING_CONNECTOR = /(?:\s*(?:[,:·–-]|\bpor\b|\ba\b|\ben\b))+\s*$/i;

/**
 * The offer stage sets the approved price apart in large type, so the headline
 * drops the copy's trailing price instead of saying it twice:
 * "Voltio + Café Patio x3 para los 3 por $8.490." → "Voltio + Café Patio x3 para los 3".
 * Only an exact match of the approved price at the end is removed; a price
 * mid-sentence keeps the line whole (the guard already pinned the amount).
 */
export function splitOfferHeadline(copy: string, offer: CreativeOffer | null): string {
    if (offer === null) return copy;
    const approved = offer.price.amount.replace(/\D/g, "");
    const matches = [...copy.matchAll(PRICE)];
    const last = matches.at(-1);
    if (last?.index === undefined || last[0].replace(/\D/g, "") !== approved) return copy;
    const after = copy.slice(last.index + last[0].length);
    if (!/^[\s.!]*$/.test(after)) return copy;
    const head = copy.slice(0, last.index).replace(TRAILING_CONNECTOR, "").trim();
    return head.length > 0 ? head : copy;
}

/** Kit lines read "Brand · line"; the logo already says the brand, so the hook drops it. */
export function withoutBrandPrefix(copy: string, brand: string): string {
    const prefix = `${brand} · `;
    const rest = copy.startsWith(prefix) ? copy.slice(prefix.length).trim() : copy;
    return rest.length > 0 ? rest : copy;
}
