import type { Playable } from "./player-machine";

/**
 * The house ad: bundled with the app (a data: URI, no network), so the screen
 * has something to show from the first frame and through any outage.
 */
const HOUSE_LOGO =
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
        // The ADverse mark alone (public/brand/adverse-mark-on-dark.svg); the player sets brand_name beside it.
        `<svg xmlns="http://www.w3.org/2000/svg" viewBox="-500 -100 1000 1000"><path d="M-500 900L-97.13 174.83A200 200 0 0 0 -43.39 195.24L-200 900Z" fill="#ede8e0"/><path d="M500 900L97.13 174.83A200 200 0 0 1 43.39 195.24L200 900Z" fill="#ede8e0"/><circle r="100" fill="#f4845f"/></svg>`,
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
                brand_name: "ADverse",
                logo_url: HOUSE_LOGO,
                copy_text: "Acércate. Hay una oferta para tu grupo.",
                accent_color: "#f4845f",
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
