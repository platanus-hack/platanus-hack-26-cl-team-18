import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
    readonly label: string;
    readonly children: ReactNode;
    readonly fallback?: ReactNode;
}

export class ErrorBoundary extends Component<Props, { error: Error | null }> {
    override state: { error: Error | null } = { error: null };

    static getDerivedStateFromError(error: Error) {
        return { error };
    }

    override componentDidCatch(error: Error, info: ErrorInfo) {
        console.error(`[${this.props.label}] render failed`, error, info.componentStack);
    }

    override render() {
        if (this.state.error) {
            return (
                this.props.fallback ?? (
                    <p className="text-danger">{this.props.label} failed to render.</p>
                )
            );
        }
        return this.props.children;
    }
}
