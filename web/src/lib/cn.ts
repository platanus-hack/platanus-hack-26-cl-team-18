import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * tailwind-merge only knows Tailwind's stock scale. Our type tiers (global.css
 * `--text-*`) would otherwise read as colors, and `text-title text-accent`
 * would silently drop the size.
 */
const twMerge = extendTailwindMerge({
    extend: {
        classGroups: {
            "font-size": [
                {
                    text: [
                        "micro",
                        "metadata",
                        "compact",
                        "body",
                        "section",
                        "title",
                        "kpi",
                        "display",
                        "hero",
                    ],
                },
            ],
        },
    },
});

export function cn(...inputs: ClassValue[]): string {
    return twMerge(clsx(inputs));
}
