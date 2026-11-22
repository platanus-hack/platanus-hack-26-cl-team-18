import type {
    ConsoleAuction,
    ConsoleMoment,
    ConsoleNext,
    CreativeReady,
    CreativeUpgraded,
} from "@/contract";
import { moneyCl } from "@/lib/format";
import { DEMO_BRANDS } from "./panels";

/**
 * "Por qué este aviso", in plain words from facts the console already has: one
 * sentence that answers it, four short steps, and one line for what was left out.
 * Pure, so every wording is unit-tested.
 */

export interface Why {
    readonly headline: string;
    readonly steps: readonly { readonly step: string; readonly text: string }[];
    readonly leftOut: string;
}

const people = (n: number) => (n === 1 ? "1 persona" : `${n} personas`);

function heldText(moment: ConsoleMoment): string {
    const [held] = moment.held_products;
    if (!held) return "";
    const brand = (held.brand_ref && DEMO_BRANDS[held.brand_ref]) ?? held.brand_ref;
    return brand ? ` con ${brand} en la mano` : ` con ${held.category} en la mano`;
}

export function explain(
    moment: ConsoleMoment | null,
    auction: ConsoleAuction | null,
    onAir: CreativeReady | CreativeUpgraded | ConsoleNext | null,
): Why | null {
    if (!moment || !auction || auction.moment_id !== moment.moment_id) return null;
    const winners = auction.winners.map((w) => w.brand_name);
    const looking = moment.looking;
    const reach =
        auction.coverage >= 0.999
            ? looking === 1
                ? "la persona que mira"
                : `las ${looking} personas`
            : `${Math.round(auction.coverage * 100)} % del grupo`;
    const headline =
        winners.length === 0
            ? "Aviso de la casa: ninguna marca supera el mínimo para este grupo."
            : winners.length === 1
              ? `${winners[0] ?? ""}, porque es la marca que más vale para ${looking === 1 ? "esta persona" : "este grupo"}.`
              : `${winners.join(" + ")}, porque juntas llegan a ${reach} y valen más que cualquier marca sola.`;
    const topics = [...moment.segments]
        .sort((a, b) => b.score - a.score)
        .slice(0, 3)
        .map((s) => s.label);
    const offer = onAir?.spec.offer ?? null;
    const leftOut = auction.exclusions
        .filter((e) =>
            ["competitor", "not_partner", "no_basket_offer", "safety"].includes(e.reason),
        )
        .map((e) => e.detail)
        .filter((detail) => detail !== "");
    return {
        headline,
        steps: [
            { step: "Vimos", text: `${people(looking)} mirando${heldText(moment)}` },
            {
                step: "Entendimos",
                text: topics.length ? topics.join(" · ") : "sin intereses claros",
            },
            {
                step: "Eligió la subasta",
                text: winners.length ? winners.join(" + ") : "aviso de la casa",
            },
            {
                step: "Oferta",
                text: offer
                    ? `${offer.label} ${moneyCl(offer.price.amount)} · $ 0 si nadie mira`
                    : "Paga solo si alguien mira",
            },
        ],
        leftOut: [
            leftOut.length ? `Quedó fuera ${leftOut.join("; ")}.` : "",
            moment.minor_likely.uncertain
                ? "Seguridad por confirmar: se bloquearon las categorías restringidas."
                : moment.minor_likely.value
                  ? "Posible menor: se bloquearon las categorías restringidas."
                  : "Nadie parece menor, así que no se bloqueó ninguna categoría.",
        ]
            .filter(Boolean)
            .join(" "),
    };
}

export interface BasketLine {
    readonly name: string;
    readonly value: string;
    readonly note: string;
    readonly tone: "win" | "base" | "other" | "blocked";
}

/** "Canastas evaluadas": the winner, the single-brand baseline, the rest, and the pairs never allowed. */
export function baskets(auction: ConsoleAuction | null): BasketLine[] {
    if (!auction) return [];
    const lines: BasketLine[] = auction.alternatives.map((option, index) => {
        const name = option.brands.join(" + ");
        const value = `${moneyCl(option.value.amount)}/s`;
        if (index === 0 && auction.winners.length > 0)
            return { name, value, note: "Gana", tone: "win" };
        if (option.brands.length === 1 && option.value.amount === auction.single_brand_value.amount)
            return {
                name: `${name} sola`,
                value,
                note: "La mejor marca sola: la base de comparación",
                tone: "base",
            };
        return { name, value, note: "Válida, pero vale menos", tone: "other" };
    });
    for (const exclusion of auction.exclusions) {
        const pair = /^(.+): misma categoría que (.+)$/.exec(exclusion.detail);
        if (exclusion.reason === "competitor" && pair?.[1] && pair[2]) {
            lines.push({
                name: `${pair[2]} + ${pair[1]}`,
                value: "no permitida",
                note: "Competidoras: nunca comparten pantalla",
                tone: "blocked",
            });
        }
    }
    return lines;
}
