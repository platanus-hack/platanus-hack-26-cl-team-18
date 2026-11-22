import { assetUrl } from "@/lib/config";
import { useState } from "react";

/**
 * A bidder's mark: the brand kit's real logo plate (never AI-drawn, invariant 4).
 * If the file can't load, it falls back to the brand's accent dot so the row
 * never shows a broken image.
 */
export function BrandLogo({
    src,
    accent,
    size = "md",
    highlight = false,
}: {
    src: string;
    accent: string | null;
    size?: "sm" | "md";
    highlight?: boolean;
}) {
    const [failed, setFailed] = useState(false);
    const box = size === "sm" ? "size-7" : "size-9";
    if (failed || !src) {
        return (
            <span aria-hidden className={`${box} flex shrink-0 items-center justify-center`}>
                <span
                    className="size-2.5 rounded-full"
                    style={{ background: accent ?? "var(--color-border)" }}
                />
            </span>
        );
    }
    return (
        <img
            src={assetUrl(src)}
            alt=""
            aria-hidden
            loading="lazy"
            decoding="async"
            draggable={false}
            onError={() => {
                setFailed(true);
            }}
            className={
                highlight
                    ? `${box} shrink-0 rounded-[22%] object-contain shadow-sm ring-2 ring-accent ring-offset-1 ring-offset-surface`
                    : `${box} shrink-0 rounded-[22%] object-contain shadow-sm ring-1 ring-border-subtle`
            }
        />
    );
}
