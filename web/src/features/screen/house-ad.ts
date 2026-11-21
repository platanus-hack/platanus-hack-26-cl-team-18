import type { Playable } from "./player-machine";

/**
 * The house ad: bundled with the app (a data: URI, no network), so the screen
 * has something to show from the first frame and through any outage.
 */
const HOUSE_LOGO =
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
        `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 80"><rect width="240" height="80" rx="16" fill="#0f1117"/><text x="120" y="52" font-family="Inter,system-ui,sans-serif" font-size="34" font-weight="700" fill="#c6f26b" text-anchor="middle">team18</text></svg>`,
    );

export const HOUSE_AD: Playable = {
    creativeId: "house",
    auctionId: null,
    kind: "house",
    spec: {
        layout_id: "solo",
        slots: [
            {
                brand_id: "house",
                brand_name: "team18",
                logo_url: HOUSE_LOGO,
                copy_text: "Acércate. Hay una oferta para tu grupo.",
                accent_color: "#c6f26b",
                hero_image_url: null,
            },
        ],
        duration_ms: 10_000,
        video_url: null,
        voiceover_url: null,
        qr_url: null,
        stages: [],
        offer: null,
        angle: null,
        copy_source: null,
        redemption_code: null,
        redeem_url: null,
    },
};
