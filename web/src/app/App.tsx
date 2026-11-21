import { ErrorBoundary } from "@/shared/ui/ErrorBoundary";
import { WorkspaceHeader } from "@/shared/ui/WorkspaceHeader";
import { Activity, Monitor, SlidersHorizontal, Store } from "lucide-react";
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
const CampaignsPage = lazy(() =>
    import("@/features/campaigns").then((m) => ({ default: m.CampaignsPage })),
);

const ROUTES: Record<string, () => React.JSX.Element> = {
    "/screen": () => <ScreenPage />,
    "/console": () => <ConsolePage />,
    "/advertiser": () => <AdvertiserPage />,
    "/clerk": () => <ClerkPage />,
    "/campaigns": () => <CampaignsPage />,
};

const TOOLS = [
    {
        href: "/console",
        icon: Activity,
        title: "Console en vivo",
        label: "Observar",
        description: "Audiencia, subastas y atención. Cada decisión, a la vista.",
    },
    {
        href: "/screen?id=demo-01",
        icon: Monitor,
        title: "Pantalla",
        label: "Reproducir",
        description: "Ofertas que se adaptan al grupo, listas para el próximo momento.",
    },
    {
        href: "/advertiser",
        icon: SlidersHorizontal,
        title: "Anunciantes",
        label: "Participar",
        description: "Elige tu marca y ajusta su puja en el mercado en vivo.",
    },
    {
        href: "/clerk?id=demo-01",
        icon: Store,
        title: "Tienda",
        label: "Gestionar",
        description: "Actualiza el stock para ofrecer lo que sí está disponible.",
    },
];

function Index() {
    return (
        <div className="paper-theme">
            <WorkspaceHeader />
            <main className="workspace-content">
                <section className="grid gap-8 border-b border-rule py-12 md:grid-cols-3 md:py-20">
                    <div className="md:col-span-2">
                        <p className="eyebrow mb-6">Publicidad para el mundo físico</p>
                        <h1 className="text-hero leading-none font-medium tracking-tight text-balance">
                            Una pantalla.
                            <br />
                            Muchas necesidades.
                            <br />
                            <span className="text-accent">Un mismo momento.</span>
                        </h1>
                    </div>
                    <div className="flex flex-col justify-end gap-6">
                        <p className="text-lg leading-relaxed text-muted">
                            Productos que se complementan. Ofertas para quienes están juntos. Marcas
                            que pagan por atención real.
                        </p>
                        <a
                            href="/console"
                            className="self-start rounded-control bg-accent px-5 py-3 font-medium text-white transition-opacity hover:opacity-90"
                        >
                            Abrir console
                        </a>
                        <p className="text-metadata text-muted">Cero atención. Cero cobro.</p>
                    </div>
                </section>
                <section className="py-10" aria-labelledby="tools-title">
                    <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
                        <h2 id="tools-title" className="text-section font-medium">
                            Tu espacio de operación
                        </h2>
                        <span className="text-metadata text-muted">Del momento a la venta</span>
                    </div>
                    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                        {TOOLS.map(({ href, icon: Icon, title, label, description }) => (
                            <a key={href} href={href} className="tool-card tool-link">
                                <Icon
                                    className="size-6 text-accent"
                                    strokeWidth={1.5}
                                    aria-hidden
                                />
                                <div>
                                    <h3 className="mb-2 text-xl font-medium tracking-tight">
                                        {title}
                                    </h3>
                                    <p className="text-body leading-relaxed text-muted">
                                        {description}
                                    </p>
                                </div>
                                <span className="mt-auto border-t border-border pt-4 text-metadata font-medium text-accent">
                                    {label}
                                </span>
                            </a>
                        ))}
                    </div>
                </section>
                <footer className="flex flex-wrap justify-between gap-3 border-t border-border py-6 text-metadata text-muted">
                    <span>Vendemos momentos, no espacios.</span>
                    <span>Sin identidad. Sin guardar imágenes ni audio.</span>
                </footer>
            </main>
        </div>
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
