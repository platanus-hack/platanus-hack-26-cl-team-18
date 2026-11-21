import {
    ConsoleBookSchema,
    type AdvertiserAutoBid,
    type AdvertiserBidMultiplier,
    type ConsoleBook,
    type StockSet,
} from "@/contract";
import { useEffect, useState } from "react";
import { backendHttpUrl } from "./config";

/**
 * The demo market's HTTP inputs (`/advertiser`, `/clerk`). Phones reach them
 * through a tunnel (ADR 0008 D3), so they use plain HTTP and poll the book.
 * Every response is validated against the generated contract.
 */

export async function fetchBook(signal?: AbortSignal, screenId = "demo-01"): Promise<ConsoleBook> {
    const response = await fetch(
        `${backendHttpUrl}/api/book?screen_id=${encodeURIComponent(screenId)}`,
        signal ? { signal } : {},
    );
    if (!response.ok) throw new Error(`book: HTTP ${response.status}`);
    return ConsoleBookSchema.parse(await response.json());
}

async function post(path: string, body: unknown): Promise<unknown> {
    const response = await fetch(`${backendHttpUrl}${path}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    return response.json();
}

let lastSeq = 0;

/** Strictly increasing across reloads (ms clock), so a late request can't win. */
function nextSeq(): number {
    lastSeq = Math.max(lastSeq + 1, Date.now());
    return lastSeq;
}

export async function setBidMultiplier(campaignId: string, multiplier: string): Promise<unknown> {
    const message: AdvertiserBidMultiplier = {
        type: "advertiser.bid_multiplier",
        v: 1,
        campaign_id: campaignId,
        multiplier,
        seq: nextSeq(),
    };
    return post("/api/advertiser/bid-multiplier", message);
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

export interface BookState {
    readonly book: ConsoleBook | null;
    readonly error: string | null;
    readonly refresh: () => void;
}

/** Poll the live book (default every second); `refresh` after a write for snappy feedback. */
export function useBook(intervalMs = 1000, screenId = "demo-01"): BookState {
    const [book, setBook] = useState<ConsoleBook | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [tick, setTick] = useState(0);
    useEffect(() => {
        const controller = new AbortController();
        const load = () => {
            fetchBook(controller.signal, screenId)
                .then((next) => {
                    setBook(next);
                    setError(null);
                })
                .catch((reason: unknown) => {
                    if (!controller.signal.aborted) setError(String(reason));
                });
        };
        load();
        const timer = setInterval(load, intervalMs);
        return () => {
            controller.abort();
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
