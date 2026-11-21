import type { ConsoleOutbound, CreativeSpec, LiveSet, LiveStatus } from "@/contract";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ConsolePage } from "./ConsolePage";

const socket = vi.hoisted(() => ({ push: (_m: ConsoleOutbound): void => {}, url: "" }));
vi.mock("@/lib/ws/typed-socket", () => ({
    TypedSocket: class {
        constructor(options: { url: string; onMessage: (m: ConsoleOutbound) => void }) {
            socket.push = options.onMessage;
            socket.url = options.url;
        }
        start() {}
        stop() {}
    },
}));

/** A fake backend: the switch, /health, and the demo market endpoints. */
let live: LiveStatus;
let failPost = false;
const requests: { path: string; body: unknown; key: string | null }[] = [];

function derive(on: boolean, mic: boolean, revision: number): LiveStatus {
    return {
        type: "live.status",
        v: 1,
        screen_id: "demo-01",
        revision,
        live: on,
        mic,
        speech_opt_in: true,
        sensing: on,
        listening: on && mic,
        ai_generation: on,
        agents: ["speech", "vision"],
        changed_at: "2026-11-21T15:00:00Z",
    };
}

const health = {
    inputs: {
        "demo-01": {
            camera_recent: true,
            speech_enabled: true,
            speech_recent: false,
            last_utterance_at: null,
            last_keyterm_count: null,
            player_connected: true,
        },
    },
};

const money = (amount: string) => ({ amount, currency: "CLP" });
const book: ConsoleOutbound = {
    type: "console.book",
    v: 1,
    screen_id: "demo-01",
    lines: [
        {
            campaign_id: "c-voltio",
            brand_id: "b-voltio",
            brand_name: "Voltio",
            category: "energy",
            accent_color: "#c6f26b",
            multiplier: "1",
            multiplier_seq: 3,
            in_stock: true,
            on_air: false,
            budget: money("50000"),
            spent: money("0"),
            reserved: money("0"),
            remaining: money("50000"),
            autobid: false,
            autobid_rule: null,
            autobid_reason: null,
        },
    ],
};

let root: Root;
let host: HTMLDivElement;

beforeEach(async () => {
    vi.useFakeTimers();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    live = derive(false, true, 0);
    failPost = false;
    requests.length = 0;
    window.localStorage.clear();
    vi.stubGlobal(
        "fetch",
        vi.fn((input: string, options?: RequestInit) => {
            const path = new URL(input, "http://x").pathname;
            const key = new Headers(options?.headers).get("X-Demo-Key");
            const body = options?.body ? (JSON.parse(options.body as string) as unknown) : null;
            if (options?.method === "POST") requests.push({ path, body, key });
            if (path === "/health") return Promise.resolve(new Response(JSON.stringify(health)));
            if (path === "/api/live" && options?.method === "POST") {
                if (failPost) return Promise.resolve(new Response("no", { status: 500 }));
                const change = body as LiveSet;
                live = derive(change.live ?? live.live, change.mic ?? live.mic, live.revision + 1);
            }
            if (path === "/api/live") return Promise.resolve(new Response(JSON.stringify(live)));
            return Promise.resolve(new Response(JSON.stringify({ outcome: "applied" })));
        }),
    );
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => {
        root.render(<ConsolePage />);
        await Promise.resolve();
    });
    await settle();
});

afterEach(async () => {
    await act(async () => {
        root.unmount();
        await Promise.resolve();
    });
    host.remove();
    window.localStorage.clear();
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

async function settle() {
    await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
    });
}

function button(name: string) {
    const found = [...host.querySelectorAll("button")].find(
        (b) => b.textContent.includes(name) || b.getAttribute("aria-label") === name,
    );
    if (!found) throw new Error(`no button "${name}" in: ${host.textContent}`);
    return found;
}

async function click(name: string) {
    await act(async () => {
        button(name).click();
        await Promise.resolve();
    });
    await settle();
}

async function push(message: ConsoleOutbound) {
    await act(async () => {
        socket.push(message);
        await Promise.resolve();
    });
}

it("subscribes as a station console", () => {
    expect(socket.url).toContain("/ws/console?live=1");
});

it("starts in standby with one calm state and no mic button, then goes live", async () => {
    expect(host.textContent).toContain("En espera");
    expect(host.textContent).toContain("Cámara lista");
    expect(host.textContent).not.toContain("Silenciar micrófono");
    await click("Iniciar en vivo");
    expect(requests[0]).toMatchObject({ path: "/api/live", body: { live: true, mic: null } });
    expect(host.textContent).toContain("Cámara analizando");
    await click("Silenciar micrófono");
    expect(requests.at(-1)?.body).toMatchObject({ live: null, mic: false });
    await click("Detener");
    expect(host.textContent).toContain("En espera");
});

it("a failed click keeps the backend's truth and says so", async () => {
    failPost = true;
    await click("Iniciar en vivo");
    expect(host.textContent).toContain("No se aplicó el cambio");
    expect(host.textContent).toContain("En espera");
});

it("shows every brand from the book, read-only without the demo key", async () => {
    await push(book);
    expect(host.querySelector('[data-brand="Voltio"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Subir puja de Voltio"]')).toBeNull();
    expect(host.textContent).toContain("abre una vez /demo/<clave>");
});

it("with the demo key remembered, bids and stock move from the home page", async () => {
    window.localStorage.setItem("team18.demoKey", "k3y");
    await push(book);
    await click("Subir puja de Voltio");
    expect(requests.at(-1)).toMatchObject({
        path: "/api/advertiser/bid-multiplier",
        key: "k3y",
        body: { campaign_id: "c-voltio", multiplier: "1.5" },
    });
    await click("Stock de Voltio");
    expect(requests.at(-1)).toMatchObject({
        path: "/api/clerk/stock",
        key: "k3y",
        body: { brand_id: "b-voltio", in_stock: false },
    });
});

it("previews the next ad while a swap waits, and explains the decision", async () => {
    const spec = {
        layout_id: "solo",
        slots: [
            {
                brand_id: "voltio",
                brand_name: "Voltio",
                logo_url: "/v.svg",
                copy_text: "Recarga",
                accent_color: "#c6f26b",
                hero_image_url: null,
            },
        ],
        duration_ms: 12000,
        video_url: null,
        voiceover_url: null,
        qr_url: null,
        stages: [],
        offer: { label: "Trío", text: "Para los 3", price: money("8490"), party_size: 3 },
        angle: null,
        copy_source: null,
        redemption_code: null,
        redeem_url: null,
    } satisfies CreativeSpec;
    await push({
        type: "console.next",
        v: 1,
        screen_id: "demo-01",
        auction_id: "a2",
        spec,
        not_before: new Date(Date.now() + 2000).toISOString(),
    });
    await settle();
    expect(host.querySelector('[aria-label="Siguiente aviso"]')?.textContent).toContain(
        "Voltio · Trío $8.490",
    );
    expect(host.textContent).toContain("Sin decisión todavía");
});
