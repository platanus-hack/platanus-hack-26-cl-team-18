import {
    CampaignGenerateSchema,
    type CampaignCatalog,
    type CampaignGenerate,
    type CampaignGenerated,
} from "@/contract";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CampaignsPage } from "./CampaignsPage";

const catalog: CampaignCatalog = {
    type: "campaign.catalog",
    v: 1,
    brands: [
        {
            slug: "voltio",
            name: "Voltio",
            category: "sports_goods",
            accent_color: "#c6f26b",
            logo_url: "/voltio.svg",
            photo_url: "/energy.webp",
            partners: ["cafe-patio"],
            age_restricted: false,
        },
        {
            slug: "cafe-patio",
            name: "Café Patio",
            category: "coffee",
            accent_color: "#f5bb84",
            logo_url: "/cafe.svg",
            photo_url: "/coffee.webp",
            partners: ["voltio"],
            age_restricted: false,
        },
        {
            slug: "malta-sur",
            name: "Malta Sur",
            category: "alcohol",
            accent_color: "#f6d15a",
            logo_url: "/beer.svg",
            photo_url: "/beer.webp",
            partners: [],
            age_restricted: true,
        },
    ],
    approved_baskets: [["cafe-patio", "voltio"]],
};

function generated(request: CampaignGenerate): CampaignGenerated {
    return {
        type: "campaign.generated",
        v: 1,
        campaign_key: `party-${request.party_size}`,
        title: "Dúo post-entreno",
        brands: request.brands,
        party_size: request.party_size,
        moment: request.moment,
        status: "draft",
        quality_score: "0.99",
        checks: ["guarded_copy"],
        compose_ms: 1.2,
        cache_hit: false,
        ai_available: false,
        spec: {
            layout_id: "duo",
            slots: catalog.brands
                .filter((brand) => request.brands.includes(brand.slug))
                .map((brand) => ({
                    brand_id: brand.slug,
                    brand_name: brand.name,
                    logo_url: brand.logo_url,
                    copy_text: "Oferta",
                    accent_color: brand.accent_color,
                    hero_image_url: "/photo.webp",
                })),
            duration_ms: 12000,
            video_url: request.media === "video" ? "/video.mp4" : null,
            voiceover_url: null,
            qr_url: null,
            stages: [
                { name: "teaser", copy_text: "Una pausa" },
                { name: "offer", copy_text: `Oferta para ${request.party_size}` },
                { name: "close", copy_text: "Aquí al lado" },
            ],
            offer: null,
            angle: "post_workout",
            copy_source: "stock",
            redemption_code: null,
            redeem_url: null,
        },
    };
}

let root: Root;
let host: HTMLDivElement;
const fetchMock = vi.fn<typeof fetch>();
let generateResponse: (request: CampaignGenerate) => Promise<unknown>;
let upgradeResponse: ((request: CampaignGenerate) => Promise<unknown>) | null;
beforeEach(async () => {
    vi.useFakeTimers();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.stubGlobal("fetch", fetchMock);
    generateResponse = (request) => Promise.resolve(generated(request));
    upgradeResponse = null;
    fetchMock.mockImplementation(async (input, options) => {
        const url =
            typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        if (url.endsWith("/catalog")) return new Response(JSON.stringify(catalog));
        if (typeof options?.body !== "string") throw new Error("Expected JSON request body");
        const request = CampaignGenerateSchema.parse(JSON.parse(options.body) as unknown);
        if (url.endsWith("/upgrade")) {
            if (upgradeResponse === null) throw new Error("upgrade called with AI off");
            return new Response(JSON.stringify(await upgradeResponse(request)));
        }
        return new Response(JSON.stringify(await generateResponse(request)));
    });
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => {
        root.render(<CampaignsPage />);
        await Promise.resolve();
    });
});
afterEach(async () => {
    await act(async () => {
        root.unmount();
        await Promise.resolve();
    });
    host.remove();
    fetchMock.mockReset();
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

async function settle() {
    await act(async () => {
        await vi.advanceTimersByTimeAsync(110);
    });
}
function button(text: string) {
    return [...host.querySelectorAll("button")].find((element) =>
        element.textContent.includes(text),
    )!;
}
async function click(element: HTMLElement) {
    await act(async () => {
        element.click();
        await Promise.resolve();
    });
}

it("renders the real shared player with both brands, product photography and optional video", async () => {
    await settle();
    expect(host.querySelector("img.creative-media")).not.toBeNull();
    expect(host.querySelector("video")?.getAttribute("src")).toContain("video.mp4");
    expect(host.querySelector(".creative-brands")?.textContent).toContain("Voltio");
    expect(host.querySelector(".creative-brands")?.textContent).toContain("Café Patio");
    expect(host.querySelector(".creative-offer-line")?.textContent).toBe("Oferta para 2");
    await click(button("Foto"));
    await settle();
    expect(host.querySelector("video")).toBeNull();
    expect(host.querySelector("img.creative-media")).not.toBeNull();
    await click(button("03 · Canje"));
    expect(host.textContent).toContain("El QR se emite al activar la campaña.");
});

it("shows the stock draft at once, then swaps in Claude's guarded rewrite", async () => {
    let finish: () => void = () => undefined;
    generateResponse = (request) => Promise.resolve({ ...generated(request), ai_available: true });
    upgradeResponse = (request) =>
        new Promise((resolve) => {
            const base = generated(request);
            finish = () => {
                resolve({
                    ...base,
                    ai_available: true,
                    spec: {
                        ...base.spec,
                        copy_source: "ai",
                        stages: base.spec.stages.map((stage) =>
                            stage.name === "close"
                                ? { ...stage, copy_text: "Muestra el QR en caja" }
                                : stage,
                        ),
                    },
                });
            };
        });
    await settle();
    expect(host.textContent).toContain("Aquí al lado"); // stock is on screen meanwhile
    await act(async () => {
        finish();
        await vi.advanceTimersByTimeAsync(10);
    });
    expect(host.textContent).toContain("Muestra el QR en caja");
    expect(host.textContent).not.toContain("Aquí al lado");
});

it("keeps the stock draft when Claude's rewrite fails", async () => {
    generateResponse = (request) => Promise.resolve({ ...generated(request), ai_available: true });
    upgradeResponse = () => Promise.reject(new Error("provider down"));
    await settle();
    await act(async () => {
        await vi.advanceTimersByTimeAsync(10);
    });
    expect(host.textContent).toContain("Aquí al lado");
    expect(host.querySelector('[role="alert"]')).toBeNull();
});

it("never calls the model when AI is off", async () => {
    await settle();
    const urls = fetchMock.mock.calls.map(([input]) => (typeof input === "string" ? input : ""));
    expect(urls.some((url) => url.endsWith("/upgrade"))).toBe(false);
});

it("drops a late campaign result after a newer group size is ready", async () => {
    await settle();
    let resolve: (data: unknown) => void = () => {};
    generateResponse = (request) =>
        request.party_size === 3
            ? new Promise((done) => {
                  resolve = done;
              })
            : Promise.resolve(generated(request));
    await click(host.querySelectorAll<HTMLButtonElement>(".studio-party-options button")[2]!);
    await settle();
    expect(button("Exportar").disabled).toBe(true);
    await click(host.querySelectorAll<HTMLButtonElement>(".studio-party-options button")[3]!);
    await settle();
    expect(host.querySelector(".creative-offer-line")?.textContent).toBe("Oferta para 4");
    await act(async () => {
        resolve(
            generated(
                CampaignGenerateSchema.parse({
                    type: "campaign.generate",
                    v: 1,
                    brands: ["voltio", "cafe-patio"],
                    party_size: 3,
                    moment: "post_workout",
                    daypart: null,
                    minor_likely: false,
                    media: "video",
                }),
            ),
        );
        await Promise.resolve();
    });
    expect(host.querySelector(".creative-offer-line")?.textContent).toBe("Oferta para 4");
});

it("handles malformed generator output and disables exporting the previous draft", async () => {
    await settle();
    generateResponse = () => Promise.resolve({ broken: true });
    await click(button("Foto"));
    await settle();
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
    expect(button("Exportar").disabled).toBe(true);
});

it("can clear a selection and excludes restricted brands when minors are protected", async () => {
    await settle();
    await click(host.querySelectorAll<HTMLButtonElement>(".studio-brand-card")[0]!);
    await click(host.querySelectorAll<HTMLButtonElement>(".studio-brand-card")[1]!);
    expect(button("Exportar").disabled).toBe(true);
    expect(host.querySelectorAll<HTMLButtonElement>(".studio-brand-card")[2]!.disabled).toBe(false);
    await click(host.querySelector<HTMLInputElement>('input[type="checkbox"]')!);
    expect(host.querySelectorAll<HTMLButtonElement>(".studio-brand-card")[2]!.disabled).toBe(true);
});
