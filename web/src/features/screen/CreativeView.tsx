import type { CreativeSlot, CreativeSpec, CreativeStageName, DistanceBand } from "@/contract";
import { assetUrl } from "@/lib/config";
import { Fragment, useState, type CSSProperties } from "react";
import { readableAccent } from "./accent";
import "./creative.css";
import { priceDigits, splitOfferHeadline, withoutBrandPrefix } from "./offer-copy";
import { useDwellStage, useViewingDistance, type AttentionSample } from "./stages";

/** Reveal the actual mounted video only after it can play; overlays stay ready. */
function VideoLoop({ url }: { url: string }) {
    const [ready, setReady] = useState(false);
    return (
        <video
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
 * House style (docs/creative-generation.md §12): one idea per stage, one accent
 * (the lead brand's) used once, the price as the largest number on screen.
 * - teaser: the hook, large, in the display face.
 * - offer: the approved offer label, the offer line without its trailing price,
 *   and the price set apart (never AI-written: it comes from `spec.offer`).
 * - close: one instruction, the QR sized to scan from ~1.5 m, code and validity.
 * A legacy spec without stages renders each slot's own copy.
 */
export function CreativeView({
    spec,
    playKey,
    readAttention,
    previewStage,
    previewDistance,
}: {
    spec: CreativeSpec;
    playKey: string;
    /** The latest screen.attention; omitted, stages follow time on air. */
    readAttention?: () => AttentionSample | null;
    previewStage?: CreativeStageName;
    /** The studio's distance preview; the live screen follows attention instead. */
    previewDistance?: DistanceBand | undefined;
}) {
    const dwellStage = useDwellStage(playKey, spec.stages, readAttention);
    const liveDistance = useViewingDistance(readAttention);
    const distance = previewDistance ?? liveDistance;
    const stage = spec.stages.find((item) => item.name === previewStage) ?? dwellStage;
    const photo = spec.slots.find((slot) => slot.hero_image_url)?.hero_image_url;
    const accent = readableAccent(spec.slots[0]?.accent_color);
    const style = (accent ? { "--creative-accent": accent } : {}) as CSSProperties;
    return (
        <div
            className="creative-canvas relative h-full w-full overflow-hidden bg-canvas"
            data-distance={distance ?? undefined}
            data-stage={stage?.name}
            style={style}
        >
            {photo && <img className="creative-media" src={assetUrl(photo)} alt="" />}
            {spec.video_url && <VideoLoop key={spec.video_url} url={spec.video_url} />}
            {(photo || spec.video_url) && <div className="creative-shade" />}
            {stage === null ? (
                <LegacySlots spec={spec} />
            ) : (
                <div className="creative-composition">
                    <header className="creative-top">
                        <BrandLockup slots={spec.slots} />
                        {spec.offer && <span className="creative-label">{spec.offer.label}</span>}
                    </header>
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
                        {stage.name === "close" && (
                            <CloseCall
                                spec={spec}
                                copy={stage.copy_text}
                                mentionsValidity={/válido 10 min/i.test(stage.copy_text)}
                            />
                        )}
                    </div>
                </div>
            )}
        </div>
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

/** No stages (the house ad, a legacy spec): logo on top, the line large at the bottom. */
function LegacySlots({ spec }: { spec: CreativeSpec }) {
    return (
        <div className="creative-legacy" data-count={spec.slots.length}>
            {spec.slots.map((slot) => (
                <section key={slot.brand_id} className="creative-legacy-slot">
                    <img
                        className="creative-legacy-logo"
                        src={assetUrl(slot.logo_url)}
                        alt={slot.brand_name}
                    />
                    <p className="creative-hook">
                        {withoutBrandPrefix(slot.copy_text, slot.brand_name)}
                    </p>
                </section>
            ))}
        </div>
    );
}

/**
 * The approved price: a small "$", large tabular digits. The hero (offer stage) sits under a
 * line that already names the group, so only the close repeats "Para N" next to the price.
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
            {party && !hero && (
                <span className="creative-price-party" aria-hidden="true">
                    {party}
                </span>
            )}
        </span>
    );
}

/** The close stage: one instruction and a QR that scans from about 1.5 m on a TV. */
function CloseCall({
    spec,
    copy,
    mentionsValidity,
}: {
    spec: CreativeSpec;
    copy: string;
    mentionsValidity: boolean;
}) {
    return (
        <div className="creative-close">
            <div className="creative-close-copy">
                <p className="creative-close-line">{copy}</p>
                <OfferPrice spec={spec} />
                {spec.qr_url === null ? (
                    <span className="creative-close-note">
                        El QR se emite al activar la campaña.
                    </span>
                ) : (
                    <div className="creative-close-meta">
                        {spec.redemption_code && (
                            <span className="creative-code">{spec.redemption_code}</span>
                        )}
                        {!mentionsValidity && <span className="creative-label">válido 10 min</span>}
                    </div>
                )}
                <span className="creative-approach">Acércate para escanear</span>
            </div>
            {spec.qr_url !== null && (
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
