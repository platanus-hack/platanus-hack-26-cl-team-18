/** The ad canvas (--palette-ink-950); accents are judged against it. */
const CANVAS = "#161412";
/** The price is large but carries the sale: hold it to body-text contrast. */
const MIN_CONTRAST = 4.5;

function linear(channel: number): number {
    const c = channel / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number {
    const at = (i: number) => linear(parseInt(hex.slice(i, i + 2), 16));
    return 0.2126 * at(1) + 0.7152 * at(3) + 0.0722 * at(5);
}

export function contrastOnCanvas(hex: string): number {
    const a = luminance(hex);
    const b = luminance(CANVAS);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

/**
 * The lead brand's accent when it reads on the dark canvas, else null (the ad
 * falls back to white). A navy or deep-red brand color must never hide the price.
 */
export function readableAccent(hex: string | null | undefined): string | null {
    if (!hex || !/^#[0-9a-f]{6}$/i.test(hex)) return null;
    return contrastOnCanvas(hex) >= MIN_CONTRAST ? hex : null;
}
