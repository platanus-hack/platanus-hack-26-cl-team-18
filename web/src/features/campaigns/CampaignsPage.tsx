import {
    CampaignCatalogSchema,
    CampaignGeneratedSchema,
    type CampaignCatalog,
    type CampaignGenerate,
    type CampaignGenerated,
    type DistanceBand,
} from "@/contract";
import { CreativeView } from "@/features/screen";
import { assetUrl, backendHttpUrl } from "@/lib/config";
import { WorkspaceHeader } from "@/shared/ui/WorkspaceHeader";
import {
    ArrowDownToLine,
    ArrowLeftRight,
    ArrowRight,
    Check,
    ChevronRight,
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
    ["hunger", "Hambre o antojo"],
    ["energy", "Pilas bajas"],
    ["queue", "Esperando en la fila"],
    ["complete_basket", "Ya tienen un producto en la mano"],
] as const;

type Daypart = NonNullable<CampaignGenerate["daypart"]>;
const DAYPARTS: readonly (readonly [Daypart | null, string])[] = [
    [null, "Siempre"],
    ["morning", "Mañana"],
    ["midday", "Mediodía"],
    ["afternoon", "Tarde"],
    ["night", "Noche"],
];

const DISTANCES: readonly (readonly [DistanceBand | undefined, string])[] = [
    ["near", "Cerca"],
    [undefined, "Media"],
    ["far", "Lejos"],
];

interface Starter {
    title: string;
    subtitle: string;
    brands: readonly string[];
    party: number;
    moment: CampaignGenerate["moment"];
    daypart: Daypart | null;
    media: CampaignGenerate["media"];
}

const STARTERS: readonly Starter[] = [
    {
        title: "Salida del gimnasio",
        subtitle: "Voltio lidera · para 3",
        brands: ["voltio", "cafe-patio"],
        party: 3,
        moment: "post_workout",
        daypart: null,
        media: "video",
    },
    {
        title: "Café de media tarde",
        subtitle: "Café Patio lidera · para 2",
        brands: ["cafe-patio", "rayo"],
        party: 2,
        moment: "coffee_break",
        daypart: "afternoon",
        media: "photo",
    },
    {
        title: "Pilas bajas",
        subtitle: "Rayo lidera · tarde",
        brands: ["rayo", "cafe-patio"],
        party: 1,
        moment: "energy",
        daypart: "afternoon",
        media: "video",
    },
    {
        title: "Completa el café",
        subtitle: "Producto en mano · para 1",
        brands: ["cafe-patio", "voltio"],
        party: 1,
        moment: "complete_basket",
        daypart: null,
        media: "video",
    },
];

/** What each server-side check guarantees, in the advertiser's words. */
const CHECKS: Record<string, readonly [string, string]> = {
    real_brand_assets: ["Logos reales", "Del kit de cada marca, nunca generados."],
    approved_pairing: ["Pareja aprobada", "Las dos marcas se aceptan como partners."],
    guarded_copy: ["Textos revisados", "Sin competidores, claims ni frases de relleno."],
    fixed_offer_price: ["Precio del catálogo", "El anuncio no puede cambiarlo."],
    minor_safety: ["Protección de menores", "Bloquea categorías restringidas si hay menores."],
};

const STAGE_LABELS = [
    ["teaser", "01 · Gancho"],
    ["offer", "02 · Oferta"],
    ["close", "03 · Canje"],
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
                : "No pudimos preparar la campaña. Intenta nuevamente.",
        );
    }
    return data;
}

function explainFailure(reason: unknown): string {
    return reason instanceof Error && reason.name !== "ZodError" && reason.name !== "TypeError"
        ? reason.message
        : "No pudimos preparar la vista previa. Intenta nuevamente.";
}

export function CampaignsPage() {
    const [catalog, setCatalog] = useState<CampaignCatalog | null>(null);
    const [brands, setBrands] = useState(["voltio", "cafe-patio"]);
    const [party, setParty] = useState(2);
    const [moment, setMoment] = useState<CampaignGenerate["moment"]>("post_workout");
    const [daypart, setDaypart] = useState<Daypart | null>(null);
    const [distance, setDistance] = useState<DistanceBand | undefined>(undefined);
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
            daypart,
            minor_likely: minor,
            media,
        }),
        [brands, party, moment, daypart, minor, media],
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
    // In slot order: the first brand leads the creative.
    const selected = brands.flatMap(
        (slug) => catalog?.brands.find((brand) => brand.slug === slug) ?? [],
    );
    return (
        <main className="paper-theme campaign-studio">
            <WorkspaceHeader />
            <div className="studio-page">
                <div className="studio-breadcrumb">
                    Workspace <ChevronRight size={13} /> Campañas <ChevronRight size={13} /> Nueva
                    campaña
                </div>
                <section className="studio-heading">
                    <div>
                        <p className="eyebrow">NUEVA CAMPAÑA</p>
                        <h1>
                            Tú armas la oferta.
                            <br />
                            <span>La pantalla elige el momento.</span>
                        </h1>
                        <p>
                            Elige las marcas, la situación y la hora. La vista previa muestra el
                            anuncio tal como sale en tienda, con el precio aprobado.
                        </p>
                    </div>
                    <button
                        className="studio-export"
                        onClick={download}
                        disabled={
                            campaign === null || busy || error !== null || brands.length === 0
                        }
                    >
                        <ArrowDownToLine size={17} /> Exportar campaña
                    </button>
                </section>
                <div className="studio-starters" aria-label="Campañas sugeridas">
                    {STARTERS.map((starter) => (
                        <button
                            key={starter.title}
                            disabled={catalog === null}
                            onClick={() => {
                                setBrands([...starter.brands]);
                                setParty(starter.party);
                                setMoment(starter.moment);
                                setDaypart(starter.daypart);
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
                            <h2>Las marcas</h2>
                            <small>Hasta 2 · la primera lidera</small>
                        </div>
                        <div className="studio-brand-grid">
                            {catalog?.brands.map((brand) => {
                                const chosen = brands.includes(brand.slug);
                                const leads = brands.length === 2 && brands[0] === brand.slug;
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
                                            {leads
                                                ? "Lidera"
                                                : chosen
                                                  ? brands.length === 2
                                                      ? "Acompaña"
                                                      : "Seleccionada"
                                                  : disabled
                                                    ? "No es partner aprobado"
                                                    : "Añadir marca"}
                                        </small>
                                    </button>
                                );
                            })}
                        </div>
                        {brands.length === 2 && (
                            <button
                                className="studio-swap"
                                onClick={() => {
                                    setBrands([...brands].reverse());
                                }}
                            >
                                <ArrowLeftRight size={15} />
                                Que lidere {selected[1]?.name ?? "la otra marca"}
                            </button>
                        )}
                        <div className="studio-partner-note">
                            <ShieldCheck size={16} />
                            <span>
                                Solo combinaciones complementarias con una oferta del catálogo.
                            </span>
                        </div>
                        <div className="studio-rule" />
                        <div className="studio-step">
                            <span>02</span>
                            <h2>El momento</h2>
                        </div>
                        <label className="studio-label" htmlFor="campaign-moment">
                            ¿Qué está pasando?
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
                        <div className="studio-label studio-party-label">¿A qué hora?</div>
                        <div className="studio-segmented" role="group" aria-label="Hora del día">
                            {DAYPARTS.map(([value, label]) => (
                                <button
                                    key={label}
                                    aria-pressed={daypart === value}
                                    onClick={() => {
                                        setDaypart(value);
                                    }}
                                >
                                    {label}
                                </button>
                            ))}
                        </div>
                        <div className="studio-label studio-party-label">
                            <Users size={14} /> Personas mirando <strong>{party}</strong>
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
                                Proteger a menores<small>Excluye categorías restringidas</small>
                            </span>
                            <ShieldCheck size={17} />
                        </label>
                        <div className="studio-rule" />
                        <div className="studio-step">
                            <span>03</span>
                            <h2>El formato</h2>
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
                                    {format === "photo" ? "Fotografía" : "Video loop"}
                                </button>
                            ))}
                        </div>
                    </aside>
                    <section className="studio-result" aria-label="Vista previa de campaña">
                        <div className="studio-preview-heading">
                            <span>
                                <span className="studio-live-dot" /> Vista previa
                            </span>
                            <span>16:9 · Pantalla en tienda</span>
                        </div>
                        <div className="studio-preview" aria-busy={busy}>
                            {campaign && brands.length > 0 ? (
                                <CreativeView
                                    spec={campaign.spec}
                                    playKey={campaign.campaign_key}
                                    previewStage={stage}
                                    previewDistance={distance}
                                />
                            ) : (
                                <div className="studio-preview-empty">
                                    <Sparkles size={26} />
                                    <p>
                                        {brands.length === 0
                                            ? "Selecciona las marcas para empezar."
                                            : error
                                              ? "Tu campaña estará aquí"
                                              : "Preparando tu momento…"}
                                    </p>
                                </div>
                            )}
                            {busy && campaign && (
                                <span className="studio-updating">Actualizando…</span>
                            )}
                        </div>
                        <div className="studio-preview-bottom">
                            <div className="studio-stages">
                                {STAGE_LABELS.map(([value, label]) => (
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
                            <div
                                className="studio-stages"
                                role="group"
                                aria-label="Distancia del público"
                            >
                                {DISTANCES.map(([value, label]) => (
                                    <button
                                        key={label}
                                        aria-pressed={distance === value}
                                        onClick={() => {
                                            setDistance(value);
                                        }}
                                    >
                                        {label}
                                    </button>
                                ))}
                            </div>
                            <span>
                                {busy
                                    ? "Componiendo…"
                                    : campaign && !error
                                      ? `Lista en ${campaign.compose_ms.toFixed(1)} ms`
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
                                    Reintentar <ArrowRight size={14} />
                                </button>
                            </div>
                        )}
                        {campaign && !error && (
                            <ol className="studio-copy" aria-label="Textos del anuncio">
                                {campaign.spec.stages.map((item) => (
                                    <li key={item.name}>
                                        <span>
                                            {STAGE_LABELS.find(([name]) => name === item.name)?.[1]}
                                        </span>
                                        <p>{item.copy_text}</p>
                                    </li>
                                ))}
                            </ol>
                        )}
                        <div className="studio-result-details">
                            <div>
                                <p className="eyebrow">
                                    {campaign?.spec.copy_source === "stock"
                                        ? "TEXTOS BASE · REVISADOS"
                                        : "TEXTOS IA · REVISADOS"}
                                </p>
                                <h2>{campaign?.title ?? "Elige las marcas"}</h2>
                                <p>
                                    {selected.map((brand) => brand.name).join(" + ")} · {party}{" "}
                                    {party === 1 ? "persona" : "personas"} ·{" "}
                                    {media === "video"
                                        ? "Loop de 6 segundos"
                                        : "Fotografía de producto"}
                                </p>
                            </div>
                            <span className="studio-draft">Borrador</span>
                        </div>
                        {campaign && !error && (
                            <section className="studio-checks" aria-label="Revisión del anuncio">
                                <header>
                                    <span>
                                        Revisión · {campaign.checks.length} de{" "}
                                        {campaign.checks.length} controles
                                    </span>
                                    <strong>
                                        Calidad {Math.round(Number(campaign.quality_score) * 100)}
                                        /100
                                    </strong>
                                </header>
                                <ul>
                                    {campaign.checks.map((check) => {
                                        const [label, detail] = CHECKS[check] ?? [check, ""];
                                        return (
                                            <li key={check}>
                                                <ShieldCheck size={15} />
                                                <span>
                                                    <strong>{label}</strong>
                                                    {detail && <small>{detail}</small>}
                                                </span>
                                            </li>
                                        );
                                    })}
                                </ul>
                            </section>
                        )}
                        <p className="studio-disclosure">
                            Imágenes ilustrativas generadas con IA. El pack exportado es un borrador
                            para revisión; no activa campañas ni cambia presupuestos.
                        </p>
                    </section>
                </div>
            </div>
        </main>
    );
}
