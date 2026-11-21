import type { CreativeSlot, CreativeSpec, CreativeStageName, DistanceBand } from "@/contract";
import { assetUrl } from "@/lib/config";
import { Fragment, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { readableAccent } from "./accent";
import { spirographPath, themeForHour, type AdTheme } from "./ad-theme";
import "./creative.css";
import { priceDigits, splitOfferHeadline, withoutBrandPrefix } from "./offer-copy";
import { useDwellStage, useViewingDistance, type AttentionSample } from "./stages";

/** Reveal the actual mounted video only after it can play; overlays stay ready. */
function VideoLoop({ url }: { url: string }) {
    const [ready, setReady] = useState(false);
    const ref = useRef<HTMLVideoElement>(null);
    // A detached <video> keeps its decoder and buffers until GC; over hours of
    // swaps that piles up. Release the media as soon as the loop leaves.
    useEffect(() => {
        const video = ref.current;
        return () => {
            if (video === null) return;
            video.pause();
            video.removeAttribute("src");
            video.load();
        };
    }, []);
    return (
        <video
            ref={ref}
            className="creative-media"
            src={assetUrl(url)}
            style={{ opacity: ready ? 1 : 0 }}
            onCanPlayThrough={() => {
                setReady(true);
            }}
            onError={() => {
                setReady(false);
            }}
            autoPlay
            muted
            loop
            playsInline
            preload="auto"
        />
    );
}

/**
 * Renders a CreativeSpec as DOM over an optional photo or video loop. Logos are
 * the brand kit's real assets (never generated); 1–3 slots share the screen.
 *
 * House style (docs/creative-generation.md §12), after the Relvo brand system:
 * one grotesk set large and tight for the line of the stage, mono caps for
 * metadata on the bottom baseline next to the brand lockup, and one accent (the
 * lead brand's) for the stroke-only figure and a single fill.
 * - light (day): a grey field, ink type, the product photo as a right panel.
 * - dark (night): the photo full-bleed under a shade, white type.
 * Stages: the hook (the price waits on the baseline), the offer line with the
 * price set apart (never AI-written: it comes from `spec.offer`), then one
 * instruction with a QR sized to scan from ~1.5 m.
 */
export function CreativeView({
    spec,
    playKey,
    readAttention,
    previewStage,
    previewDistance,
    previewTheme,
}: {
    spec: CreativeSpec;
    playKey: string;
    /** The latest screen.attention; omitted, stages follow time on air. */
    readAttention?: () => AttentionSample | null;
    previewStage?: CreativeStageName;
    /** The studio's distance preview; the live screen follows attention instead. */
    previewDistance?: DistanceBand | undefined;
    /** The studio's day/night preview; the live screen follows the store's clock. */
    previewTheme?: AdTheme | undefined;
}) {
    const dwellStage = useDwellStage(playKey, spec.stages, readAttention);
    const liveDistance = useViewingDistance(readAttention);
    const [clockTheme] = useState(() => themeForHour(new Date().getHours()));
    const theme = previewTheme ?? clockTheme;
    const distance = previewDistance ?? liveDistance;
    const stage = spec.stages.find((item) => item.name === previewStage) ?? dwellStage;
    const photo = spec.slots.find((slot) => slot.hero_image_url)?.hero_image_url;
    const media = Boolean(photo ?? spec.video_url);
    const accent = readableAccent(spec.slots[0]?.accent_color);
    const style = (accent ? { "--creative-accent": accent } : {}) as CSSProperties;
    const seed = spec.slots.map((slot) => slot.brand_name).join("+");
    return (
        <div
            className="creative-canvas relative h-full w-full overflow-hidden"
            data-theme={theme}
            data-media={media ? "yes" : "no"}
            data-distance={distance ?? undefined}
            data-stage={stage?.name ?? "legacy"}
            style={style}
        >
            {(theme === "light" || !media) && <Figure seed={seed} />}
            {media && (
                <div className="creative-visual">
                    {photo && <img className="creative-media" src={assetUrl(photo)} alt="" />}
                    {spec.video_url && <VideoLoop key={spec.video_url} url={spec.video_url} />}
                    <div className="creative-shade" />
                </div>
            )}
            <div className="creative-composition">
                {stage === null ? (
                    <LegacyStory spec={spec} />
                ) : (
                    <div
                        key={`${stage.name}:${stage.copy_text}`}
                        className="creative-story"
                        style={{ animation: "stage-in var(--duration-crossfade) ease-out both" }}
                    >
                        {stage.name === "teaser" && (
                            <p className="creative-hook">{stage.copy_text}</p>
                        )}
                        {stage.name === "offer" && (
                            <>
                                <p className="creative-offer-line">
                                    {splitOfferHeadline(stage.copy_text, spec.offer)}
                                </p>
                                <OfferPrice spec={spec} hero />
                            </>
                        )}
                        {stage.name === "close" && <CloseCall spec={spec} copy={stage.copy_text} />}
                    </div>
                )}
                <footer className="creative-baseline">
                    <BrandLockup slots={spec.slots} />
                    <Baseline spec={spec} stage={stage?.name ?? null} copy={stage?.copy_text} />
                </footer>
            </div>
            {stage?.name === "close" && spec.qr_url !== null && (
                <div className="creative-qr">
                    <img
                        className="creative-qr-image"
                        src={assetUrl(spec.qr_url)}
                        alt="QR para canjear la oferta"
                        style={{ imageRendering: "pixelated" }}
                    />
                    <span className="creative-qr-caption">Escanea</span>
                </div>
            )}
        </div>
    );
}

/** The signature line figure: stroke-only, seeded by the basket, in the lead brand's accent. */
function Figure({ seed }: { seed: string }) {
    const d = useMemo(() => spirographPath(seed), [seed]);
    return (
        <svg className="creative-figure" viewBox="-1.02 -1.02 2.04 2.04" aria-hidden="true">
            <path d={d} fill="none" vectorEffect="non-scaling-stroke" />
        </svg>
    );
}

/** The co-brand lockup: real logos, names, and "×" between partners. */
function BrandLockup({ slots }: { slots: CreativeSlot[] }) {
    return (
        <div className="creative-brands">
            {slots.map((slot, index) => (
                <Fragment key={slot.brand_id}>
                    {index > 0 && (
                        <span className="creative-times" aria-hidden="true">
                            ×
                        </span>
                    )}
                    <span className="creative-brand">
                        <img
                            className="creative-brand-logo"
                            src={assetUrl(slot.logo_url)}
                            alt={slot.brand_name}
                        />
                        <span>{slot.brand_name}</span>
                    </span>
                </Fragment>
            ))}
        </div>
    );
}

/**
 * Mono metadata on the baseline. During the hook it carries the price, so a
 * three-second glance still leaves with a number; later stages show it large.
 */
function Baseline({
    spec,
    stage,
    copy,
}: {
    spec: CreativeSpec;
    stage: CreativeStageName | null;
    copy: string | undefined;
}) {
    const offer = spec.offer;
    if (offer === null || stage === null) return null;
    return (
        <div className="creative-meta">
            <span className="creative-label">{offer.label}</span>
            {stage === "teaser" && (
                <span className="creative-label creative-meta-price">
                    ${priceDigits(offer)}
                    {offer.party_size > 1 && ` · Para ${offer.party_size}`}
                </span>
            )}
            {stage === "close" && spec.qr_url !== null && !/válido 10 min/i.test(copy ?? "") && (
                <span className="creative-label">válido 10 min</span>
            )}
        </div>
    );
}

/** No stages (the house ad, a legacy spec): the lead line large; partners' lines below. */
function LegacyStory({ spec }: { spec: CreativeSpec }) {
    const [lead, ...rest] = spec.slots;
    if (lead === undefined) return null;
    return (
        <div className="creative-story">
            <p className="creative-hook">{withoutBrandPrefix(lead.copy_text, lead.brand_name)}</p>
            {rest.map((slot) => (
                <p key={slot.brand_id} className="creative-offer-line">
                    {withoutBrandPrefix(slot.copy_text, slot.brand_name)}
                </p>
            ))}
        </div>
    );
}

/**
 * The approved price: a small "$", large tabular digits. The hero (offer stage) carries the
 * group as an accent-filled tag; the close repeats the price smaller next to the code.
 */
function OfferPrice({ spec, hero = false }: { spec: CreativeSpec; hero?: boolean }) {
    const offer = spec.offer;
    if (offer === null) return null;
    const party = offer.party_size > 1 ? `Para ${offer.party_size}` : null;
    return (
        <span className={`creative-price ${hero ? "is-hero" : ""}`}>
            {/* Screen readers and tests read "Para 3 · $8.490"; the eye reads the digits. */}
            <span className="sr-only">
                {party ? `${party} · ` : ""}${priceDigits(offer)}
            </span>
            <span className="creative-price-figure" aria-hidden="true">
                <span className="creative-price-currency">$</span>
                {priceDigits(offer)}
            </span>
            {party && (
                <span className="creative-price-party" aria-hidden="true">
                    {party}
                </span>
            )}
        </span>
    );
}

/** The close stage's copy column; the QR itself sits over the photo side of the canvas. */
function CloseCall({ spec, copy }: { spec: CreativeSpec; copy: string }) {
    return (
        <div className="creative-close">
            <p className="creative-close-line">{copy}</p>
            <OfferPrice spec={spec} />
            {spec.qr_url === null ? (
                <span className="creative-close-note">El QR se emite al activar la campaña.</span>
            ) : (
                spec.redemption_code && (
                    <span className="creative-code">{spec.redemption_code}</span>
                )
            )}
            <span className="creative-approach">Acércate para escanear</span>
        </div>
    );
}
