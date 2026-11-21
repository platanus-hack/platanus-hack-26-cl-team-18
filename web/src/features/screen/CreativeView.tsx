import type { CreativeSpec } from "@/contract";
import { assetUrl } from "@/lib/config";
import { useState } from "react";
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
}: {
    spec: CreativeSpec;
    playKey: string;
    /** The latest screen.attention; omitted, stages follow time on air. */
    readAttention?: () => AttentionSample | null;
}) {
    const stage = useDwellStage(playKey, spec.stages, readAttention);
    const columns =
        spec.slots.length === 1
            ? "grid-cols-1"
            : spec.slots.length === 2
              ? "grid-cols-2"
              : "grid-cols-3";
    return (
        <div className="relative h-full w-full overflow-hidden bg-canvas">
            {spec.video_url && <VideoLoop key={spec.video_url} url={spec.video_url} />}
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
                <div className="relative flex h-full flex-col items-center justify-between gap-10 p-16 text-center">
                    <div className="flex items-center justify-center gap-16">
                        {spec.slots.map((slot) => (
                            <img
                                key={slot.brand_id}
                                className="h-[16vh] max-w-[30vw] object-contain"
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
                        ))}
                    </div>
                    <p
                        key={`${stage.name}:${stage.copy_text}`}
                        className="max-w-[80vw] text-[6vw] leading-tight font-semibold text-balance"
                        style={{ animation: "stage-in var(--duration-crossfade) ease-out both" }}
                    >
                        {stage.copy_text}
                    </p>
                    <div className="flex min-h-[12vh] items-center justify-center">
                        {stage.name === "offer" && spec.offer && (
                            <span
                                className="rounded-container px-10 py-4 text-[3vw] font-bold text-canvas"
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
            )}
        </div>
    );
}

/** The close stage: a scannable QR (about 1.5 m on a TV) with its code, or text only. */
function CloseCall({ spec }: { spec: CreativeSpec }) {
    if (spec.qr_url === null) {
        return <span className="text-[2.5vw] text-muted">QR · válido 10 min</span>;
    }
    return (
        <div className="flex items-center gap-10">
            <div className="rounded-container bg-fg p-[2vh]">
                <img
                    className="block aspect-square h-[28vh]"
                    src={assetUrl(spec.qr_url)}
                    alt="QR para canjear la oferta"
                    style={{ imageRendering: "pixelated" }}
                />
            </div>
            <div className="flex flex-col items-start gap-2 text-left">
                {spec.redemption_code && (
                    <span className="font-mono text-[3vw] font-bold tracking-widest tabular-nums">
                        {spec.redemption_code}
                    </span>
                )}
                <span className="text-[2.5vw] text-muted">válido 10 min</span>
            </div>
        </div>
    );
}
