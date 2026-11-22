import type { Playable } from "./player-machine";

/**
 * The house ad: bundled with the app (a data: URI, no network), so the screen
 * has something to show from the first frame and through any outage.
 */
const HOUSE_LOGO =
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
        // The ADverse mark (public/brand/adverse-mark-on-dark.svg) with the brand loop: the beams
        // rise, the dot lands, then a ring leaves the dot every 2.4 s. Still under reduced motion.
        // The player sets brand_name beside it.
        `<svg xmlns="http://www.w3.org/2000/svg" viewBox="-500 -100 1000 1000" overflow="visible"><style>` +
            `.b{transform-origin:0 900px;animation:r .7s cubic-bezier(.2,.8,.2,1) both}` +
            `.d{transform-box:fill-box;transform-origin:center;animation:l .55s .45s cubic-bezier(.3,1.6,.5,1) both,p 2.4s 1.4s ease-in-out infinite}` +
            `.g{transform-box:fill-box;transform-origin:center;opacity:0;animation:g 2.4s 1.4s cubic-bezier(.1,.6,.3,1) infinite}` +
            `@keyframes r{from{transform:scaleY(0)}to{transform:none}}` +
            `@keyframes l{from{transform:translateY(-120%) scale(.6);opacity:0}to{transform:none;opacity:1}}` +
            `@keyframes p{0%,18%,100%{transform:none}8%{transform:scale(1.12)}}` +
            `@keyframes g{0%{opacity:.7;transform:none}60%,100%{opacity:0;transform:scale(2.4)}}` +
            `@media (prefers-reduced-motion:reduce){.b,.d,.g{animation:none}}` +
            `</style><circle class="g" r="100" fill="none" stroke="#f4845f" stroke-width="18"/><path class="b" d="M-500 900L-97.13 174.83A200 200 0 0 0 -43.39 195.24L-200 900Z" fill="#ede8e0"/><path class="b" d="M500 900L97.13 174.83A200 200 0 0 1 43.39 195.24L200 900Z" fill="#ede8e0"/><circle class="d" r="100" fill="#f4845f"/></svg>`,
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
                copy_text: "Acércate. Hay una oferta para ti.",
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
