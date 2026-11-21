import type { CreativeSpec } from "@/contract";
import { assetUrl } from "@/lib/config";
import { useDwellStage } from "./stages";

/**
 * Renders a CreativeSpec as DOM over an optional video loop. Logos are the
 * brand kit's real assets (never generated). 1–3 slots share the screen.
 *
 * A spec with dwell stages shows the brands' logos on top and one big line that
 * walks teaser → offer → close; the offer stage adds the group offer badge
 * (price from the brands, never from the copywriter). A legacy spec without
 * stages renders each slot's own copy.
 */
export function CreativeView({ spec, playKey }: { spec: CreativeSpec; playKey: string }) {
    const stage = useDwellStage(playKey, spec.stages);
    const columns =
        spec.slots.length === 1
            ? "grid-cols-1"
            : spec.slots.length === 2
              ? "grid-cols-2"
              : "grid-cols-3";
    return (
        <div className="relative h-full w-full overflow-hidden bg-canvas">
            {spec.video_url && (
                <video
                    className="absolute inset-0 h-full w-full object-cover"
                    src={assetUrl(spec.video_url)}
                    autoPlay
                    muted
                    loop
                    playsInline
                    preload="auto"
                />
            )}
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
                    <div className="flex h-[12vh] items-center justify-center">
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
                        {stage.name === "close" && (
                            <span className="text-[2.5vw] text-muted">QR · válido 10 min</span>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
