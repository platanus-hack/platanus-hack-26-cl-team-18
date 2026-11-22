import type { Playable } from "./player-machine";

/**
 * The house ad: bundled with the app (a data: URI, no network), so the screen
 * has something to show from the first frame and through any outage.
 */
const HOUSE_LOGO =
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
        `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 80"><rect width="240" height="80" rx="16" fill="#13131e"/><circle cx="50" cy="40" r="7" fill="#d0ff0b"/><text x="132" y="51" font-family="Instrument Sans,system-ui,sans-serif" font-size="32" font-weight="600" fill="#ededf2" text-anchor="middle">advisor</text></svg>`,
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
                brand_name: "advisor",
                logo_url: HOUSE_LOGO,
                copy_text: "Acércate. Hay una oferta para tu grupo.",
                accent_color: "#d0ff0b",
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
