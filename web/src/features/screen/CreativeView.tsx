import type { CreativeSpec } from "@/contract";
import { assetUrl } from "@/lib/config";

/**
 * Renders a CreativeSpec as DOM over an optional video loop. Logos are the
 * brand kit's real assets (never generated). 1–3 slots share the screen.
 */
export function CreativeView({ spec }: { spec: CreativeSpec }) {
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
        </div>
    );
}
