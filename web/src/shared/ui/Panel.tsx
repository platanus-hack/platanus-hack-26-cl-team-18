import type { ReactNode } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "./card";
import { ErrorBoundary } from "./ErrorBoundary";

/** A console card. Each has its own error boundary: one crash can't blank the page. */
export function Panel({
    title,
    aside,
    children,
    className,
}: {
    title: string;
    aside?: ReactNode;
    children: ReactNode;
    className?: string;
}) {
    return (
        <Card className={className}>
            <CardHeader>
                <CardTitle>{title}</CardTitle>
                {aside && <span className="text-compact text-muted tabular-nums">{aside}</span>}
            </CardHeader>
            <CardContent>
                <ErrorBoundary label={title}>{children}</ErrorBoundary>
            </CardContent>
        </Card>
    );
}
