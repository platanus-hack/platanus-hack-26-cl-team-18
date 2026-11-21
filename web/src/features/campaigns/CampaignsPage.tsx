import {
    CampaignCatalogSchema,
    CampaignGeneratedSchema,
    type CampaignCatalog,
    type CampaignGenerate,
    type CampaignGenerated,
} from "@/contract";
import { CreativeView } from "@/features/screen";
import { assetUrl, backendHttpUrl } from "@/lib/config";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { AppShell, PageHeader } from "@/shared/ui/page";
import {
    ArrowDownToLine,
    ArrowRight,
    Check,
    Film,
    Image,
    Plus,
    ShieldCheck,
    Sparkles,
    Users,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import "./campaigns.css";

const MOMENTS = [
    ["post_workout", "Después de entrenar"],
    ["coffee_break", "Una pausa para café"],
    ["hunger", "Algo rico para comer"],
    ["queue", "Mientras esperan"],
    ["complete_basket", "Completar la compra"],
] as const;

const STARTERS = [
    {
        title: "Recarga en equipo",
        subtitle: "Voltio + Café Patio · 3 personas",
        brands: ["voltio", "cafe-patio"],
        party: 3,
        moment: "post_workout",
        media: "video",
    },
    {
        title: "La pausa de la tarde",
        subtitle: "Café Patio + Rayo · 2 personas",
        brands: ["cafe-patio", "rayo"],
        party: 2,
        moment: "coffee_break",
        media: "photo",
    },
    {
        title: "Completa tu café",
        subtitle: "Café Patio + Voltio · 1 persona",
        brands: ["cafe-patio", "voltio"],
        party: 1,
        moment: "complete_basket",
        media: "video",
    },
] as const;

async function readJson(
    path: string,
    signal: AbortSignal,
    body?: CampaignGenerate,
): Promise<unknown> {
    const response = await fetch(
        `${backendHttpUrl}/api/campaigns/${path}`,
        body
            ? {
                  method: "POST",
                  headers: { "content-type": "application/json" },
                  body: JSON.stringify(body),
                  signal,
              }
            : { signal },
    );
    const data: unknown = await response.json();
    if (!response.ok) {
        const detail =
            typeof data === "object" && data !== null && "detail" in data ? data.detail : null;
        throw new Error(
            typeof detail === "string"
                ? detail
                : "No se pudo preparar la campaña. Inténtalo otra vez.",
        );
    }
    return data;
}

function explainFailure(reason: unknown): string {
    return reason instanceof Error && reason.name !== "ZodError" && reason.name !== "TypeError"
        ? reason.message
        : "No se pudo preparar la vista previa. Inténtalo otra vez.";
}

export function CampaignsPage() {
    const [catalog, setCatalog] = useState<CampaignCatalog | null>(null);
    const [brands, setBrands] = useState(["voltio", "cafe-patio"]);
    const [party, setParty] = useState(2);
    const [moment, setMoment] = useState<CampaignGenerate["moment"]>("post_workout");
    const [media, setMedia] = useState<CampaignGenerate["media"]>("video");
    const [minor, setMinor] = useState(false);
    const [stage, setStage] = useState<"teaser" | "offer" | "close">("offer");
    const [campaign, setCampaign] = useState<CampaignGenerated | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [working, setBusy] = useState(true);
    const [responseFor, setResponseFor] = useState("");
    const [reload, setReload] = useState(0);
    useEffect(() => {
        const controller = new AbortController();
        void readJson("catalog", controller.signal)
            .then((data) => {
                if (!controller.signal.aborted) setCatalog(CampaignCatalogSchema.parse(data));
            })
            .catch((reason: unknown) => {
                if (!controller.signal.aborted) {
                    setError(explainFailure(reason));
                    setBusy(false);
                }
            });
        return () => {
            controller.abort();
        };
    }, [reload]);
    const request = useMemo<CampaignGenerate>(
        () => ({
            type: "campaign.generate",
            v: 1,
            brands,
            party_size: party,
            moment,
            minor_likely: minor,
            media,
        }),
        [brands, party, moment, minor, media],
    );
    const requestKey = JSON.stringify(request);
    const busy = brands.length > 0 && (working || responseFor !== requestKey) && error === null;
    useEffect(() => {
        if (catalog === null || request.brands.length === 0) return;
        const controller = new AbortController();
        const timer = setTimeout(() => {
            setBusy(true);
            setError(null);
            void readJson("generate", controller.signal, request)
                .then((data) => {
                    if (!controller.signal.aborted) {
                        setCampaign(CampaignGeneratedSchema.parse(data));
                        setResponseFor(requestKey);
                        setBusy(false);
                    }
                })
                .catch((reason: unknown) => {
                    if (!controller.signal.aborted) {
                        setError(explainFailure(reason));
                        setResponseFor(requestKey);
                        setBusy(false);
                    }
                });
        }, 100);
        return () => {
            clearTimeout(timer);
            controller.abort();
        };
    }, [catalog, request, requestKey]);
    const download = () => {
        if (campaign === null || busy || error !== null || brands.length === 0) return;
        const url = URL.createObjectURL(
            new Blob([JSON.stringify(campaign, null, 2)], { type: "application/json" }),
        );
        const link = document.createElement("a");
        link.href = url;
        link.download = `campana-${campaign.campaign_key}.json`;
        link.click();
        URL.revokeObjectURL(url);
    };
    const selected = catalog?.brands.filter((brand) => brands.includes(brand.slug)) ?? [];
    return (
        <AppShell>
            <div className="campaign-studio flex flex-col gap-6">
                <PageHeader
                    title="Nueva campaña"
                    description="Elige dos marcas complementarias, el momento y el formato."
                    actions={
                        <Button
                            variant="primary"
                            onClick={download}
                            disabled={
                                campaign === null || busy || error !== null || brands.length === 0
                            }
                        >
                            <ArrowDownToLine size={16} aria-hidden /> Exportar
                        </Button>
                    }
                />
                <div className="studio-starters" aria-label="Campañas sugeridas">
                    {STARTERS.map((starter) => (
                        <button
                            key={starter.title}
                            disabled={catalog === null}
                            onClick={() => {
                                setBrands([...starter.brands]);
                                setParty(starter.party);
                                setMoment(starter.moment);
                                setMedia(starter.media);
                            }}
                        >
                            <Sparkles size={17} />
                            <span>
                                <strong>{starter.title}</strong>
                                <small>{starter.subtitle}</small>
                            </span>
                            <ArrowRight size={15} />
                        </button>
                    ))}
                </div>
                <div className="studio-workspace">
                    <aside className="studio-controls" aria-label="Configurar campaña">
                        <div className="studio-step">
                            <span>01</span>
                            <h2>Marcas</h2>
                            <small>Hasta 2</small>
                        </div>
                        <div className="studio-brand-grid">
                            {catalog?.brands.map((brand) => {
                                const chosen = brands.includes(brand.slug);
                                const compatible = brands.every(
                                    (slug) => slug === brand.slug || brand.partners.includes(slug),
                                );
                                const disabled =
                                    !chosen &&
                                    (brands.length === 2 ||
                                        !compatible ||
                                        (minor && brand.age_restricted));
                                return (
                                    <button
                                        key={brand.slug}
                                        className={`studio-brand-card ${chosen ? "is-selected" : ""}`}
                                        disabled={disabled}
                                        aria-pressed={chosen}
                                        onClick={() => {
                                            setBrands(
                                                chosen
                                                    ? brands.filter((slug) => slug !== brand.slug)
                                                    : [...brands, brand.slug],
                                            );
                                        }}
                                    >
                                        <div
                                            className="studio-brand-photo"
                                            style={{
                                                backgroundImage: brand.photo_url
                                                    ? `url(${assetUrl(brand.photo_url)})`
                                                    : undefined,
                                            }}
                                        >
                                            <span style={{ background: brand.accent_color }}>
                                                {chosen ? <Check size={12} /> : <Plus size={12} />}
                                            </span>
                                        </div>
                                        <strong>{brand.name}</strong>
                                        <small>
                                            {chosen
                                                ? "Seleccionada"
                                                : disabled
                                                  ? "No combina"
                                                  : "Agregar"}
                                        </small>
                                    </button>
                                );
                            })}
                        </div>
                        <div className="studio-partner-note">
                            <ShieldCheck size={16} />
                            <span>Solo se combinan marcas socias con una oferta aprobada.</span>
                        </div>
                        <div className="studio-rule" />
                        <div className="studio-step">
                            <span>02</span>
                            <h2>Momento</h2>
                        </div>
                        <label className="studio-label" htmlFor="campaign-moment">
                            Situación
                        </label>
                        <select
                            id="campaign-moment"
                            value={moment}
                            onChange={(event) => {
                                setMoment(event.target.value as CampaignGenerate["moment"]);
                            }}
                        >
                            {MOMENTS.map(([value, label]) => (
                                <option key={value} value={value}>
                                    {label}
                                </option>
                            ))}
                        </select>
                        <div className="studio-label studio-party-label">
                            <Users size={14} /> Personas <strong>{party}</strong>
                        </div>
                        <div className="studio-party-options">
                            {[1, 2, 3, 4, 6, 12].map((count) => (
                                <button
                                    key={count}
                                    aria-pressed={party === count}
                                    onClick={() => {
                                        setParty(count);
                                    }}
                                >
                                    {count}
                                </button>
                            ))}
                        </div>
                        <label className="studio-minor">
                            <input
                                type="checkbox"
                                checked={minor}
                                onChange={(event) => {
                                    setMinor(event.target.checked);
                                    if (
                                        event.target.checked &&
                                        selected.some((brand) => brand.age_restricted)
                                    )
                                        setBrands(["cafe-patio"]);
                                }}
                            />
                            <span>
                                Posible menor
                                <small>Bloquea alcohol y categorías restringidas</small>
                            </span>
                            <ShieldCheck size={17} />
                        </label>
                        <div className="studio-rule" />
                        <div className="studio-step">
                            <span>03</span>
                            <h2>Formato</h2>
                        </div>
                        <div className="studio-format">
                            {(["photo", "video"] as const).map((format) => (
                                <button
                                    key={format}
                                    aria-pressed={media === format}
                                    onClick={() => {
                                        setMedia(format);
                                    }}
                                >
                                    {format === "photo" ? <Image size={17} /> : <Film size={17} />}
                                    {format === "photo" ? "Foto" : "Video"}
                                </button>
                            ))}
                        </div>
                    </aside>
                    <section className="studio-result" aria-label="Vista previa de campaña">
                        <div className="studio-preview-heading">
                            <span>Vista previa</span>
                            <span>16:9</span>
                        </div>
                        <div className="studio-preview" aria-busy={busy}>
                            {campaign && brands.length > 0 ? (
                                <CreativeView
                                    spec={campaign.spec}
                                    playKey={campaign.campaign_key}
                                    previewStage={stage}
                                />
                            ) : (
                                <div className="studio-preview-empty">
                                    <Sparkles size={26} />
                                    <p>
                                        {brands.length === 0
                                            ? "Selecciona las marcas para empezar."
                                            : error
                                              ? "Sin vista previa"
                                              : "Cargando…"}
                                    </p>
                                </div>
                            )}
                            {busy && campaign && <span className="studio-updating">Cargando…</span>}
                        </div>
                        <div className="studio-preview-bottom">
                            <div className="studio-stages">
                                {(
                                    [
                                        ["teaser", "Atraer"],
                                        ["offer", "Ofrecer"],
                                        ["close", "Cerrar"],
                                    ] as const
                                ).map(([value, label]) => (
                                    <button
                                        key={value}
                                        aria-pressed={stage === value}
                                        onClick={() => {
                                            setStage(value);
                                        }}
                                    >
                                        {label}
                                    </button>
                                ))}
                            </div>
                            <span>
                                {busy
                                    ? "Cargando…"
                                    : campaign && !error
                                      ? campaign.compose_ms < 1
                                          ? "Compuesta en <1 ms"
                                          : `Compuesta en ${campaign.compose_ms.toFixed(0)} ms`
                                      : "Sin vista previa"}
                            </span>
                        </div>
                        {error && (
                            <div className="studio-error" role="alert">
                                {error}
                                <button
                                    onClick={() => {
                                        setReload((value) => value + 1);
                                    }}
                                >
                                    Reintentar
                                </button>
                            </div>
                        )}
                        <div className="studio-result-details">
                            <div>
                                <h2>{campaign?.title ?? "Sin título"}</h2>
                                <p>
                                    {selected.map((brand) => brand.name).join(" + ")} · {party}{" "}
                                    {party === 1 ? "persona" : "personas"} ·{" "}
                                    {media === "video" ? "Video de 6 s" : "Foto de producto"}
                                </p>
                            </div>
                            <Badge appearance="soft">Borrador</Badge>
                        </div>
                        <p className="studio-disclosure">
                            Imágenes de producto generadas con IA. Logos, precios y textos vienen
                            del kit de cada marca. Exportar no activa la campaña.
                        </p>
                    </section>
                </div>
            </div>
        </AppShell>
    );
}
