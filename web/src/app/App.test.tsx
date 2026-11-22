import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "./App";

vi.mock("@/lib/ws/typed-socket", () => ({
    TypedSocket: class {
        start() {}
        stop() {}
        send() {}
    },
}));

let root: Root;
let host: HTMLDivElement;

beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.stubGlobal(
        "fetch",
        vi.fn(() => Promise.reject(new Error("no backend in this test"))),
    );
    window.localStorage.clear();
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
});

afterEach(async () => {
    await act(async () => {
        root.unmount();
        await Promise.resolve();
    });
    host.remove();
    window.history.pushState({}, "", "/");
    window.localStorage.clear();
    vi.unstubAllGlobals();
});

async function open(path: string) {
    window.history.pushState({}, "", path);
    await act(async () => {
        root.render(<App />);
        await Promise.resolve();
    });
    // Pages are lazy chunks: wait until one has rendered.
    for (let i = 0; i < 100 && host.querySelector("nav") === null; i++) {
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 20));
        });
    }
}

const links = () => [...host.querySelectorAll("nav a")].map((a) => a.getAttribute("href"));

it("the home page is the admin console with the live switch", async () => {
    await open("/");
    expect(host.querySelector('[aria-label="En vivo · demo-01"]')).not.toBeNull();
    expect(links()).toEqual(["/", "/screen?id=demo-01", "/advertisers"]);
});

it("the advertiser setup asks for access when this browser has no key", async () => {
    await open("/advertisers");
    for (let i = 0; i < 50 && !host.textContent.includes("Necesitas acceso"); i++) {
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 20));
        });
    }
    expect(host.querySelector("h1")?.textContent).toBe("Anunciantes");
    expect(host.textContent).toContain("Necesitas acceso de anunciante");
    expect(fetch).not.toHaveBeenCalledWith(
        expect.stringContaining("/api/advertiser/profiles"),
        expect.anything(),
    );
});

it("the advertiser setup lists the brands once the key is known", async () => {
    window.localStorage.setItem("team18.demoKey", "k3y");
    const profile = {
        campaign_id: "c-1",
        brand_name: "Voltio",
        category: "sports_goods",
        accent_color: "#c6f26b",
        logo_url: "data:image/svg+xml;base64,PHN2Zy8+",
        tagline: null,
        age_restricted: false,
        broad_match: true,
        audiences: [
            {
                segment_id: "408",
                label: "Fitness",
                rate_per_attention_s: { amount: "8", currency: "CLP" },
            },
        ],
        partners: [],
        solo_offers: [],
        competitors: ["Rayo"],
    };
    vi.stubGlobal(
        "fetch",
        vi.fn((url: string) =>
            url.includes("/api/advertiser/profiles")
                ? Promise.resolve(
                      new Response(
                          JSON.stringify({
                              type: "advertiser.profiles",
                              v: 1,
                              profiles: [profile],
                          }),
                      ),
                  )
                : Promise.reject(new Error("no book in this test")),
        ),
    );
    await open("/advertisers");
    for (let i = 0; i < 50 && !host.textContent.includes("Voltio"); i++) {
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 20));
        });
    }
    expect(host.textContent).toContain("Voltio");
    expect(host.textContent).toContain("Bebida energética");
});

it.each(["/advertiser", "/clerk", "/campaigns", "/dev", "/demo", "/demo/k3y/admin"])(
    "%s is not a page: it shows the console",
    async (path) => {
        await open(path);
        expect(host.querySelector('[aria-label="En vivo · demo-01"]')).not.toBeNull();
        expect(links().some((href) => href?.includes("/demo/"))).toBe(false);
    },
);

it("the hidden hub lists the demo tools and this browser keeps them in the nav", async () => {
    // The backend accepts this key (a keyed GET of the book), so the browser keeps it.
    vi.stubGlobal(
        "fetch",
        vi.fn((input: string, init?: RequestInit) =>
            input.endsWith("/api/book") && new Headers(init?.headers).get("X-Demo-Key") === "k3y"
                ? Promise.resolve(new Response("{}"))
                : Promise.reject(new Error("no backend in this test")),
        ),
    );
    await open("/demo/k3y");
    expect(host.textContent).toContain("Herramientas para mostrar el producto");
    expect(links()).toEqual(
        expect.arrayContaining([
            "/demo/k3y/advertiser",
            "/demo/k3y/clerk?id=demo-01",
            "/demo/k3y/campaigns",
            "/demo/k3y/dev?screen=demo-01",
        ]),
    );
    await act(async () => {
        root.unmount();
        await Promise.resolve();
    });
    root = createRoot(host);
    await open("/");
    expect(links()).toEqual(expect.arrayContaining(["/demo/k3y", "/demo/k3y/advertiser"]));
});
