import { cn } from "@/lib/cn";
import { LoaderCircle } from "lucide-react";

const SIZE = { sm: "size-3.5", md: "size-4", lg: "size-6" } as const;

/** The one loading glyph (relvo shared/ui/spinner.tsx); still under reduced motion. */
export function Spinner({
    size = "md",
    label,
    className,
}: {
    size?: keyof typeof SIZE;
    label?: string;
    className?: string;
}) {
    const glyph = (
        <LoaderCircle
            aria-hidden
            className={cn(
                "shrink-0 motion-safe:animate-spin motion-reduce:opacity-60",
                SIZE[size],
                className,
            )}
        />
    );
    if (!label) return glyph;
    return (
        <span role="status" className="inline-flex items-center">
            {glyph}
            <span className="sr-only">{label}</span>
        </span>
    );
}
