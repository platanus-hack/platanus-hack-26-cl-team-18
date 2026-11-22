import { cn } from "@/lib/cn";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { Spinner } from "./spinner";

/**
 * Relvo's button (shared/ui/button.base.tsx): one shape, Instrument Sans 500,
 * sentence case.
 *
 * - `primary`: fluor fill, black text. The one main action of a page.
 * - `outline` (default): 1px control edge on the surface. Everything else.
 * - `ghost`: flat, hover fill only. `destructive`: outline in danger ink.
 * - `link`: the selection hue, underline on hover.
 */
const controlEdge =
    "rounded-control border border-control-edge bg-surface text-fg hover:bg-surface-hover active:bg-surface-pressed";

const buttonVariants = cva(
    "inline-flex items-center justify-center gap-2 whitespace-nowrap text-compact font-medium transition-colors duration-(--duration-base) ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-canvas disabled:pointer-events-none disabled:opacity-50 aria-disabled:opacity-50 active:translate-y-px motion-reduce:active:translate-y-0",
    {
        variants: {
            variant: {
                primary:
                    "rounded-control border border-transparent bg-primary text-on-primary hover:bg-primary-hover",
                outline: controlEdge,
                destructive:
                    "rounded-control border border-(--state-danger-edge) bg-surface text-danger hover:bg-danger-soft",
                ghost: "rounded-control text-fg hover:bg-surface-hover",
                link: "text-accent underline-offset-4 hover:underline",
            },
            size: {
                default: "h-10 px-4",
                sm: "h-8 px-3",
                lg: "h-11 px-5",
                icon: "size-10",
                "icon-sm": "size-8 hit-target",
            },
        },
        defaultVariants: { variant: "outline", size: "default" },
    },
);

export interface ButtonProps
    extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
    /** Render the child (e.g. an `<a>`) with button styles. */
    asChild?: boolean;
    /** Shows a spinner and ignores clicks; the button keeps focus. */
    loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
    (
        { className, variant, size, asChild = false, loading = false, children, onClick, ...props },
        ref,
    ) => {
        const Comp = asChild ? Slot : "button";
        return (
            <Comp
                ref={ref}
                className={cn(buttonVariants({ variant, size }), className)}
                aria-disabled={loading || undefined}
                aria-busy={loading || undefined}
                onClick={
                    loading
                        ? (event) => {
                              event.preventDefault();
                          }
                        : onClick
                }
                {...props}
            >
                {asChild ? (
                    children
                ) : (
                    <>
                        {loading && <Spinner />}
                        {children}
                    </>
                )}
            </Comp>
        );
    },
);
Button.displayName = "Button";
