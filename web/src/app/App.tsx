import { ErrorBoundary } from "@/shared/ui/ErrorBoundary";
import { Button } from "@/shared/ui/button";
import { Card } from "@/shared/ui/card";
import { AppShell, PageHeader } from "@/shared/ui/page";
import { Activity, Bug, ChevronRight, Monitor, SlidersHorizontal, Store } from "lucide-react";
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

const ROUTES: Record<string, () => React.JSX.Element> = {
    "/screen": () => <ScreenPage />,
    "/console": () => <ConsolePage />,
    "/advertiser": () => <AdvertiserPage />,
    "/clerk": () => <ClerkPage />,
    "/campaigns": () => <CampaignsPage />,
    "/dev": () => <DevPage />,
};

const TOOLS = [
    {
        href: "/console",
        icon: Activity,
        title: "Consola",
        description: "Audiencia, subasta, latencia y cobros de cada pantalla.",
    },
    {
        href: "/screen?id=demo-01",
        icon: Monitor,
        title: "Pantalla",
        description: "El reproductor que ve la gente en la tienda.",
    },
    {
        href: "/advertiser",
        icon: SlidersHorizontal,
        title: "Pujas",
        description: "Sube o baja la puja de una marca en vivo.",
    },
    {
        href: "/clerk?id=demo-01",
        icon: Store,
        title: "Stock",
        description: "Marca un producto sin stock y sale de la oferta.",
    },
    {
        href: "/dev?screen=demo-01",
        icon: Bug,
        title: "Dev / QA",
        description: "YOLO, VLM, voz, subasta y creatividad en vivo, con el aviso al aire.",
    },
];

function Index() {
    return (
        <AppShell>
            <PageHeader
                title="Avisos en canasta para pantallas en tienda"
                description="Dos marcas complementarias comparten la pantalla y pagan solo por segundo de atención."
                actions={
                    <Button asChild variant="primary">
                        <a href="/console">Abrir consola</a>
                    </Button>
                }
            />
            <Card className="divide-y divide-border-subtle">
                {TOOLS.map(({ href, icon: Icon, title, description }) => (
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
            <p className="text-compact text-muted">
                Sin identidad: no se guardan imágenes ni audio.
            </p>
        </AppShell>
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
