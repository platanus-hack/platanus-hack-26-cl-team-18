import type { CreativeSpec } from "@/contract";
import { assetUrl } from "@/lib/config";
import { useState } from "react";
import "./creative.css";
import { useDwellStage, type AttentionSample } from "./stages";

/** Reveal the actual mounted video only after it can play; overlays stay ready. */
function VideoLoop({ url }: { url: string }) {
    const [ready, setReady] = useState(false);
    return (
        <video
            className="absolute inset-0 h-full w-full object-cover"
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
 * Renders a CreativeSpec as DOM over an optional video loop. Logos are the
 * brand kit's real assets (never generated). 1–3 slots share the screen.
 *
 * A spec with dwell stages shows the brands' logos on top and one big line that
 * walks teaser → offer → close; the offer stage adds the group offer badge
 * (price from the brands, never from the copywriter). A legacy spec without
 * stages renders each slot's own copy.
 */
export function CreativeView({
    spec,
    playKey,
    readAttention,
    previewStage,
}: {
    spec: CreativeSpec;
    playKey: string;
    /** The latest screen.attention; omitted, stages follow time on air. */
    readAttention?: () => AttentionSample | null;
    previewStage?: "teaser" | "offer" | "close";
}) {
    const dwellStage = useDwellStage(playKey, spec.stages, readAttention);
    const stage = spec.stages.find((item) => item.name === previewStage) ?? dwellStage;
    const photo = spec.slots.find((slot) => slot.hero_image_url)?.hero_image_url;
    const columns =
        spec.slots.length === 1
            ? "grid-cols-1"
            : spec.slots.length === 2
              ? "grid-cols-2"
              : "grid-cols-3";
    return (
        <div className="creative-canvas relative h-full w-full overflow-hidden bg-canvas">
            {photo && <img className="creative-photo" src={assetUrl(photo)} alt="" />}
            {spec.video_url && <VideoLoop key={spec.video_url} url={spec.video_url} />}
            {(photo || spec.video_url) && <div className="creative-shade" />}
            {stage === null ? (
                <div className={`relative grid h-full ${columns} gap-8 p-16`}>
                    {spec.slots.map((slot) => (
                        <section
                            key={slot.brand_id}
                            className="flex flex-col items-center justify-center gap-10 rounded-container p-10 text-center"
                            style={
                                slot.accent_color
                                    ? { boxShadow: `inset 0 -6px 0 ${slot.accent_color}` }
                                    : undefined
                            }
                        >
                            {slot.hero_image_url && (
                                <img
                                    className="max-h-[40vh] object-contain"
                                    src={assetUrl(slot.hero_image_url)}
                                    alt=""
                                />
                            )}
                            <img
                                className="h-[14vh] max-w-full object-contain"
                                src={assetUrl(slot.logo_url)}
                                alt={slot.brand_name}
                            />
                            <p className="text-display leading-tight font-semibold text-balance">
                                {slot.copy_text}
                            </p>
                        </section>
                    ))}
                </div>
            ) : (
                <div className="creative-composition">
                    <div className="creative-brands">
                        {spec.slots.map((slot) => (
                            <div
                                className="creative-brand"
                                key={slot.brand_id}
                                style={{ borderColor: slot.accent_color ?? "var(--color-accent)" }}
                            >
                                <img
                                    className="creative-brand-logo"
                                    src={assetUrl(slot.logo_url)}
                                    alt={slot.brand_name}
                                    style={
                                        slot.accent_color
                                            ? {
                                                  filter: `drop-shadow(0 0 2.5rem ${slot.accent_color}55)`,
                                              }
                                            : undefined
                                    }
                                />
                                <span>{slot.brand_name}</span>
                            </div>
                        ))}
                    </div>
                    <div className="creative-story">
                        {spec.offer && <span className="creative-eyebrow">{spec.offer.label}</span>}
                        <p
                            key={`${stage.name}:${stage.copy_text}`}
                            className="creative-headline"
                            style={{
                                animation: "stage-in var(--duration-crossfade) ease-out both",
                            }}
                        >
                            {stage.copy_text}
                        </p>
                        <div className="creative-action">
                            {stage.name === "offer" && spec.offer && (
                                <span
                                    className="creative-price"
                                    style={{
                                        background:
                                            spec.slots[0]?.accent_color ?? "var(--color-accent)",
                                    }}
                                >
                                    {spec.offer.party_size > 1
                                        ? `Para ${spec.offer.party_size} · `
                                        : ""}
                                    ${Number(spec.offer.price.amount).toLocaleString("es-CL")}
                                </span>
                            )}
                            {stage.name === "close" && <CloseCall spec={spec} />}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

/** The close stage: a scannable QR (about 1.5 m on a TV) with its code, or text only. */
function CloseCall({ spec }: { spec: CreativeSpec }) {
    if (spec.qr_url === null) {
        return <span className="creative-close-note">El QR se emite al activar la campaña.</span>;
    }
    return (
        <div className="creative-close">
            <div className="creative-qr">
                <img
                    className="creative-qr-image"
                    src={assetUrl(spec.qr_url)}
                    alt="QR para canjear la oferta"
                    style={{ imageRendering: "pixelated" }}
                />
            </div>
            <div className="flex flex-col items-start gap-2 text-left">
                {spec.redemption_code && (
                    <span className="creative-code">{spec.redemption_code}</span>
                )}
                <span className="creative-close-note">válido 10 min</span>
            </div>
        </div>
    );
}
