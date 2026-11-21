import { demoHref, demoRoute, rememberDemoKey, type DemoPage } from "@/lib/demo";
import { ErrorBoundary } from "@/shared/ui/ErrorBoundary";
import { Card } from "@/shared/ui/card";
import { AppShell, PageHeader } from "@/shared/ui/page";
import { Bug, ChevronRight, Megaphone, SlidersHorizontal, Store } from "lucide-react";
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
const DevPage = lazy(() => import("@/features/dev").then((m) => ({ default: m.DevPage })));
const CampaignsPage = lazy(() =>
    import("@/features/campaigns").then((m) => ({ default: m.CampaignsPage })),
);

/** The real station: the player, and the operator console, which is also the home page. */
const ROUTES: Record<string, () => React.JSX.Element> = {
    "/": () => <ConsolePage />,
    "/screen": () => <ScreenPage />,
    "/console": () => <ConsolePage />,
};

/** The hidden demo surface (`/demo/<key>/…`); the backend checks the key on every call. */
const DEMO_ROUTES: Record<DemoPage, () => React.JSX.Element> = {
    advertiser: () => <AdvertiserPage />,
    clerk: () => <ClerkPage />,
    campaigns: () => <CampaignsPage />,
    dev: () => <DevPage />,
};

function demoTools(key: string) {
    return [
        {
            href: demoHref(key, "advertiser"),
            icon: SlidersHorizontal,
            title: "Pujas",
            description: "Guerra de pujas: sube o baja la puja de una marca en vivo.",
        },
        {
            href: demoHref(key, "clerk", "?id=demo-01"),
            icon: Store,
            title: "Stock",
            description: "Marca un producto sin stock y sale de la oferta.",
        },
        {
            href: demoHref(key, "campaigns"),
            icon: Megaphone,
            title: "Campañas",
            description: "Borradores de avisos en canasta desde el catálogo aprobado.",
        },
        {
            href: demoHref(key, "dev", "?screen=demo-01"),
            icon: Bug,
            title: "Dev / QA",
            description: "YOLO, VLM, voz, subasta y creatividad en vivo, con el aviso al aire.",
        },
    ];
}

function ToolList({ tools }: { tools: readonly ReturnType<typeof demoTools>[number][] }) {
    return (
        <Card className="divide-y divide-border-subtle">
            {tools.map(({ href, icon: Icon, title, description }) => (
                <a
                    key={href}
                    href={href}
                    className="flex items-center gap-4 px-4 py-4 transition-colors hover:bg-surface-hover"
                >
                    <span className="grid size-9 shrink-0 place-items-center rounded-control bg-accent-soft text-(--ui-selected-text)">
                        <Icon className="size-4" aria-hidden />
                    </span>
                    <span className="min-w-0">
                        <span className="block font-medium">{title}</span>
                        <span className="block text-compact text-muted">{description}</span>
                    </span>
                    <ChevronRight className="ml-auto size-4 shrink-0 text-muted" aria-hidden />
                </a>
            ))}
        </Card>
    );
}

function DemoHub({ demo }: { demo: string }) {
    return (
        <AppShell>
            <PageHeader
                title="Demo"
                description="Herramientas para mostrar el producto. No enlaces esta página: la clave va en la URL."
            />
            <ToolList tools={demoTools(demo)} />
            <p className="text-compact text-muted">
                Escenas guionadas sin cámara:{" "}
                <code>uv run python -m scripts.demo.rehearse duo</code> (duo, trio, held, empty).
            </p>
        </AppShell>
    );
}

export function App() {
    const path = window.location.pathname.replace(/\/+$/, "") || "/";
    const demo = demoRoute(path);
    if (demo) rememberDemoKey(demo.key); // this browser now shows the demo tools in the nav
    const page = demo ? (
        demo.page ? (
            DEMO_ROUTES[demo.page]()
        ) : (
            <DemoHub demo={demo.key} />
        )
    ) : (
        (ROUTES[path]?.() ?? <ConsolePage />)
    );
    return (
        <ErrorBoundary label="app">
            <Suspense fallback={null}>{page}</Suspense>
        </ErrorBoundary>
    );
}
