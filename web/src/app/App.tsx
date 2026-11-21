import { ErrorBoundary } from "@/shared/ui/ErrorBoundary";
import { lazy, Suspense } from "react";

// Each route is its own chunk: the kiosk player never downloads console code.
const ScreenPage = lazy(() => import("@/features/screen").then((m) => ({ default: m.ScreenPage })));
const ConsolePage = lazy(() =>
    import("@/features/console").then((m) => ({ default: m.ConsolePage })),
);
const AdvertiserPage = lazy(() =>
    import("@/features/advertiser").then((m) => ({ default: m.AdvertiserPage })),
);
const ClerkPage = lazy(() => import("@/features/clerk").then((m) => ({ default: m.ClerkPage })));

const ROUTES: Record<string, () => React.JSX.Element> = {
    "/screen": () => <ScreenPage />,
    "/console": () => <ConsolePage />,
    "/advertiser": () => <AdvertiserPage />,
    "/clerk": () => <ClerkPage />,
};

function Index() {
    return (
        <main className="flex min-h-screen flex-col items-center justify-center gap-4">
            <h1 className="text-section font-semibold">team18</h1>
            <nav className="flex gap-4 text-body">
                <a className="text-accent underline" href="/screen?id=demo-01">
                    /screen
                </a>
                <a className="text-accent underline" href="/console">
                    /console
                </a>
                <a className="text-accent underline" href="/advertiser">
                    /advertiser
                </a>
                <a className="text-accent underline" href="/clerk?id=demo-01">
                    /clerk
                </a>
            </nav>
        </main>
    );
}

export function App() {
    const path = window.location.pathname.replace(/\/+$/, "") || "/";
    const page = ROUTES[path]?.() ?? <Index />;
    return (
        <ErrorBoundary label="app">
            <Suspense fallback={null}>{page}</Suspense>
        </ErrorBoundary>
    );
}
