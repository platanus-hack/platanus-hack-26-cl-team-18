import type { ConsoleAuction, ConsoleBook, MoneyDTO } from "@/contract";
import type { Tone } from "@/shared/ui/badge";
import { EXCLUSION, MATCH } from "./labels";

/**
 * The bid table: one row per brand, joining the book (budget, stock, multiplier,
 * always present) with the last auction (bid and result, once someone was seen).
 */

export interface BidRow {
    readonly campaignId: string;
    readonly brandId: string;
    readonly brand: string;
    readonly accent: string | null;
    readonly multiplier: string;
    readonly multiplierSeq: number;
    readonly autobid: boolean;
    readonly inStock: boolean;
    readonly onAir: boolean;
    readonly bidPerAs: MoneyDTO | null;
    readonly remaining: MoneyDTO;
    readonly budget: MoneyDTO;
    readonly result: { readonly label: string; readonly tone: Tone } | null;
    /** Why it was excluded, in plain words ("Rayo: misma categoría que Voltio"). */
    readonly why: string | null;
    readonly category: string;
    /** The audience interest the bid reached ("Coffee → Pastelería"), its strongest match. */
    readonly match: string | null;
    /** How it matched: "exacta · 100 %", "relacionada · 40 %". */
    readonly matchKind: string | null;
    /** What the bid is worth for this audience, per screen-second. */
    readonly valuePerS: MoneyDTO | null;
    /** valuePerS against the best bid (bar width, presentation only). */
    readonly valueShare: number;
    /** What a winner actually pays per attention-second (≤ its bid). */
    readonly pays: MoneyDTO | null;
    /** The result's reason in a few words, under the result. */
    readonly reason: string | null;
}

/** "Rayo: misma categoría que Voltio" → "misma categoría que Voltio". */
function withoutBrand(detail: string, brand: string): string {
    return detail.startsWith(`${brand}: `) ? detail.slice(brand.length + 2) : detail;
}

export function bidRows(book: ConsoleBook, auction: ConsoleAuction | null): BidRow[] {
    const best = Math.max(
        0,
        ...(auction?.bids ?? []).map((b) => Number(b.value_per_s?.amount ?? 0)),
    );
    return book.lines.map((line) => {
        const bid = auction?.bids.find((b) => b.campaign_id === line.campaign_id) ?? null;
        const exclusion = auction?.exclusions.find((e) => e.campaign_id === line.campaign_id);
        const won = auction?.winners.some((w) => w.campaign_id === line.campaign_id) ?? false;
        const result: BidRow["result"] =
            auction === null
                ? null
                : won
                  ? { label: "Gana", tone: "success" }
                  : exclusion
                    ? { label: EXCLUSION[exclusion.reason], tone: "warning" }
                    : bid
                      ? { label: "Pierde", tone: "neutral" }
                      : { label: "Sin interés", tone: "neutral" };
        const strongest = bid ? [...bid.matches].sort((a, b) => b.weight - a.weight)[0] : undefined;
        const winner = auction?.winners.find((w) => w.campaign_id === line.campaign_id);
        const firstWinner = auction?.winners[0];
        const reason = winner
            ? winner === firstWinner
                ? "mejor valor para este grupo"
                : `socia aprobada de ${firstWinner?.brand_name ?? ""}`
            : exclusion?.detail
              ? withoutBrand(exclusion.detail, line.brand_name)
              : bid
                ? "vale menos que la canasta ganadora"
                : null;
        return {
            campaignId: line.campaign_id,
            brandId: line.brand_id,
            brand: line.brand_name,
            accent: line.accent_color ?? null,
            multiplier: line.multiplier,
            multiplierSeq: line.multiplier_seq,
            autobid: line.autobid,
            inStock: line.in_stock,
            onAir: line.on_air,
            bidPerAs: bid?.bid_per_as ?? null,
            remaining: line.remaining,
            budget: line.budget,
            result,
            why: exclusion?.detail ? exclusion.detail : null,
            category: line.category,
            match: strongest
                ? strongest.bid_segment_id === strongest.segment_id
                    ? strongest.segment_label || strongest.segment_id
                    : `${strongest.bid_segment_label || strongest.bid_segment_id} → ${strongest.segment_label || strongest.segment_id}`
                : null,
            matchKind: strongest
                ? `${MATCH[strongest.kind]} · ${Math.round(strongest.weight * 100)} %`
                : null,
            valuePerS: bid?.value_per_s ?? null,
            valueShare: bid?.value_per_s && best > 0 ? Number(bid.value_per_s.amount) / best : 0,
            pays: winner?.price_per_as ?? null,
            reason,
        };
    });
}

const MIN = 0.5;
const MAX = 5;

/** The next multiplier on a ±0.5 step within the backend's bounds; null at the edge. */
export function stepMultiplier(current: string, direction: 1 | -1): string | null {
    const value = Math.round((Number(current) + direction * 0.5) * 2) / 2;
    if (!Number.isFinite(value) || value < MIN || value > MAX) return null;
    return value.toFixed(1);
}

/** Share of the budget left, for the bar (presentation only). */
export function budgetLeft(row: BidRow): number {
    const budget = Number(row.budget.amount);
    return budget > 0 ? Math.max(0, Math.min(1, Number(row.remaining.amount) / budget)) : 0;
}

function rank(row: BidRow): number {
    if (row.result?.label === "Gana") return 0;
    if (row.onAir) return 1;
    if (row.bidPerAs && !row.why) return 2;
    if (row.why || !row.inStock) return 3;
    return 4;
}

/**
 * Brands that matter now first; brands that didn't bid on this moment folded into
 * one line. Before anyone was seen every brand shows, so standby lists them all.
 */
export function splitRows(rows: readonly BidRow[]): { shown: BidRow[]; idle: BidRow[] } {
    const sorted = [...rows].sort(
        (a, b) =>
            rank(a) - rank(b) || Number(b.bidPerAs?.amount ?? 0) - Number(a.bidPerAs?.amount ?? 0),
    );
    const idle = sorted.filter((row) => row.result?.label === "Sin interés" && rank(row) === 4);
    return { shown: sorted.filter((row) => !idle.includes(row)), idle };
}
