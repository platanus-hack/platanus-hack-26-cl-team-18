import {
    AdvertiserProfilesSchema,
    ConsoleBookSchema,
    type AdvertiserAutoBid,
    type AdvertiserBidMultiplier,
    type AdvertiserBudgetTopUp,
    type AdvertiserProfiles,
    type ConditionsSet,
    type ConsoleBook,
    type StockSet,
} from "@/contract";
import { useEffect, useState } from "react";
import { backendHttpUrl } from "./config";
import { demoHeaders } from "./demo";

/**
 * The demo market's HTTP inputs (`/demo/<key>/advertiser`, `/demo/<key>/clerk`), sent
 * with the demo key (lib/demo.ts). Phones reach them
 * through a tunnel (ADR 0008 D3), so they use plain HTTP and poll the book.
 * Every response is validated against the generated contract.
 */

export async function fetchBook(signal?: AbortSignal, screenId = "demo-01"): Promise<ConsoleBook> {
    const response = await fetch(
        `${backendHttpUrl}/api/book?screen_id=${encodeURIComponent(screenId)}`,
        signal ? { signal, headers: demoHeaders() } : { headers: demoHeaders() },
    );
    if (!response.ok) throw new Error(`book: HTTP ${response.status}`);
    return ConsoleBookSchema.parse(await response.json());
}

/**
 * A write that hasn't answered by now is abandoned (a phone on a stalled tunnel would
 * otherwise wait minutes, and the slider's later moves with it). Every write here is
 * safe to resend: bids carry a seq, top-ups an idempotency key, stock is a set.
 */
const POST_TIMEOUT_MS = 8_000;

async function post(path: string, body: unknown): Promise<unknown> {
    const controller = new AbortController();
    const timer = setTimeout(() => {
        controller.abort();
    }, POST_TIMEOUT_MS);
    try {
        const response = await fetch(`${backendHttpUrl}${path}`, {
            method: "POST",
            headers: { "content-type": "application/json", ...demoHeaders() },
            body: JSON.stringify(body),
            signal: controller.signal,
        });
        if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
        return await response.json();
    } finally {
        clearTimeout(timer);
    }
}

let lastSeq = 0;

/** Strictly increasing across reloads (ms clock), so a late request can't win. */
function nextSeq(afterSeq = 0): number {
    lastSeq = Math.max(lastSeq + 1, afterSeq + 1, Date.now());
    return lastSeq;
}

export async function setBidMultiplier(
    campaignId: string,
    multiplier: string,
    afterSeq = 0,
): Promise<number> {
    const message: AdvertiserBidMultiplier = {
        type: "advertiser.bid_multiplier",
        v: 1,
        campaign_id: campaignId,
        multiplier,
        seq: nextSeq(afterSeq),
    };
    const result = await post("/api/advertiser/bid-multiplier", message);
    if (
        typeof result !== "object" ||
        result === null ||
        !("outcome" in result) ||
        (result.outcome !== "applied" && result.outcome !== "duplicate")
    ) {
        throw new Error("Bid superseded by another controller; retry against the latest market");
    }
    return message.seq;
}

/** Rule-based auto-bidding on/off; a manual slider move turns it off server-side. */
export async function setAutoBid(campaignId: string, enabled: boolean): Promise<unknown> {
    const message: AdvertiserAutoBid = {
        type: "advertiser.autobid",
        v: 1,
        campaign_id: campaignId,
        enabled,
        target_cost_per_redemption: null,
    };
    return post("/api/advertiser/autobid", message);
}

/** Each campaign's setup (audiences, partners, offers, rivals) for the self-serve page. */
export async function fetchProfiles(signal?: AbortSignal): Promise<AdvertiserProfiles> {
    const response = await fetch(
        `${backendHttpUrl}/api/advertiser/profiles`,
        signal ? { signal, headers: demoHeaders() } : { headers: demoHeaders() },
    );
    if (!response.ok) throw new Error(`profiles: HTTP ${response.status}`);
    return AdvertiserProfilesSchema.parse(await response.json());
}

/**
 * Add whole pesos to a campaign's budget. The caller keeps `idempotencyKey` for a retry of
 * the same top-up, so a lost response never charges the advertiser twice.
 */
export async function topUpBudget(
    campaignId: string,
    amountClp: number,
    idempotencyKey: string,
): Promise<"applied" | "duplicate"> {
    const message: AdvertiserBudgetTopUp = {
        type: "advertiser.budget_top_up",
        v: 1,
        campaign_id: campaignId,
        amount: String(Math.trunc(amountClp)),
        currency: "CLP",
        idempotency_key: idempotencyKey,
    };
    const result = await post("/api/advertiser/budget", message);
    if (
        typeof result !== "object" ||
        result === null ||
        !("outcome" in result) ||
        (result.outcome !== "applied" && result.outcome !== "duplicate")
    ) {
        throw new Error("Unexpected top-up response");
    }
    return result.outcome;
}

export async function setStock(
    screenId: string,
    brandId: string,
    inStock: boolean,
): Promise<unknown> {
    const message: StockSet = {
        type: "stock.set",
        v: 1,
        screen_id: screenId,
        brand_id: brandId,
        in_stock: inStock,
    };
    return post("/api/clerk/stock", message);
}

/** Pin the venue weather for the demo (re-auctions the screen), or `null` for the real one. */
export async function setConditions(
    weather: { temperatureC: number; raining: boolean } | null,
): Promise<unknown> {
    const message: ConditionsSet = {
        type: "conditions.set",
        v: 1,
        live: weather === null,
        temperature_c: weather?.temperatureC ?? 20,
        raining: weather?.raining ?? false,
    };
    return post("/api/conditions", message);
}

export interface BookState {
    readonly book: ConsoleBook | null;
    readonly error: string | null;
    readonly refresh: () => void;
}

/** A book request older than this is abandoned for a fresh one. */
const HUNG_BOOK_MS = 5_000;

/** Poll the live book (default every second); `refresh` after a write for snappy feedback. */
export function useBook(intervalMs = 1000, screenId = "demo-01"): BookState {
    const [book, setBook] = useState<ConsoleBook | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [tick, setTick] = useState(0);
    useEffect(() => {
        let stopped = false;
        let inFlight: AbortController | null = null;
        let startedAt = 0;
        let latestRequest = 0;
        const load = () => {
            // At most one request in flight: a slow one is waited for, a hung one replaced.
            if (inFlight !== null && Date.now() - startedAt < HUNG_BOOK_MS) return;
            inFlight?.abort();
            const controller = new AbortController();
            inFlight = controller;
            startedAt = Date.now();
            const request = ++latestRequest;
            fetchBook(controller.signal, screenId)
                .finally(() => {
                    if (inFlight === controller) inFlight = null;
                })
                .then((next) => {
                    if (stopped || request !== latestRequest) return;
                    setBook(next);
                    setError(null);
                })
                .catch((reason: unknown) => {
                    if (!stopped && request === latestRequest) setError(String(reason));
                });
        };
        load();
        const timer = setInterval(load, intervalMs);
        return () => {
            stopped = true;
            inFlight?.abort();
            clearInterval(timer);
        };
    }, [intervalMs, tick, screenId]);
    return {
        book,
        error,
        refresh: () => {
            setTick((t) => t + 1);
        },
    };
}
