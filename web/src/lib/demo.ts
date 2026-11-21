/**
 * The hidden demo surface (app/api/demo_access.py). The real station is
 * `/screen` and `/console`; everything built to *show* the product lives under
 * `/demo/<key>/…`, and its API calls carry the key as `X-Demo-Key`. Without
 * the right key the backend answers 404, so a guessed path shows nothing.
 */

export const DEMO_PAGES = ["advertiser", "clerk", "campaigns", "dev"] as const;
export type DemoPage = (typeof DEMO_PAGES)[number];

const DEMO_PATH = /^\/demo\/([A-Za-z0-9_-]{1,128})(?:\/([a-z]+))?\/?$/;

export interface DemoRoute {
    readonly key: string;
    /** null: the hub itself (`/demo/<key>`); undefined page names are not demo routes. */
    readonly page: DemoPage | null;
}

export function demoRoute(pathname: string = window.location.pathname): DemoRoute | null {
    const match = DEMO_PATH.exec(pathname);
    if (!match) return null;
    const [, key, page] = match;
    if (key === undefined) return null;
    if (page === undefined) return { key, page: null };
    return (DEMO_PAGES as readonly string[]).includes(page)
        ? { key, page: page as DemoPage }
        : null;
}

/** The demo key of the current page, or null on the real station's pages. */
export function demoKey(pathname?: string): string | null {
    return demoRoute(pathname)?.key ?? null;
}

/** A link inside the demo surface; `query` keeps e.g. `?id=demo-01`. */
export function demoHref(key: string, page: DemoPage | null, query = ""): string {
    return `/demo/${encodeURIComponent(key)}${page ? `/${page}` : ""}${query}`;
}

/** Headers for a demo API call (empty off the demo surface: the backend will 404). */
export function demoHeaders(pathname?: string): Record<string, string> {
    const key = demoKey(pathname);
    return key === null ? {} : { "X-Demo-Key": key };
}
