import { ScanLine } from "lucide-react";

const links = [
    { href: "/console", label: "Console" },
    { href: "/advertiser", label: "Anunciantes" },
    { href: "/clerk", label: "Tienda" },
    { href: "/screen?id=demo-01", label: "Pantalla" },
];

/** Shared wayfinding for the workspace; the fullscreen player has its own shell. */
export function WorkspaceHeader() {
    return (
        <header className="workspace-nav">
            <a href="/" className="workspace-brand" aria-label="team18 · Inicio">
                <span className="workspace-mark">
                    <ScanLine className="size-5" aria-hidden />
                </span>
                team18
                <span className="text-metadata font-normal tracking-normal text-muted">
                    / basket ads
                </span>
            </a>
            <nav className="workspace-links" aria-label="Navegación principal">
                {links.map(({ href, label }) => (
                    <a
                        key={href}
                        href={href}
                        aria-current={
                            window.location.pathname === href.split("?")[0] ? "page" : undefined
                        }
                    >
                        {label}
                    </a>
                ))}
            </nav>
        </header>
    );
}
