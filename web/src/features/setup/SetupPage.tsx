import type { AdvertiserOffer, AdvertiserProfile, BookLine } from "@/contract";
import { categoryLabel } from "@/lib/categories";
import { assetUrl } from "@/lib/config";
import { demoHref, demoKey, rememberedDemoKey } from "@/lib/demo";
import { decimalCl, moneyCl } from "@/lib/format";
import {
    fetchProfiles,
    setAutoBid,
    setBidMultiplier,
    topUpBudget,
    useBook,
} from "@/lib/market-api";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Card } from "@/shared/ui/card";
import { Metric } from "@/shared/ui/metric";
import { AppShell, EmptyState, PageHeader } from "@/shared/ui/page";
import {
    ArrowLeft,
    ArrowRight,
    Check,
    ShieldCheck,
    ShoppingBasket,
    Swords,
    Users,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
    maxRate,
    nextStep,
    parseStep,
    parseTopUp,
    previousStep,
    stepIndex,
    STEPS,
    STRATEGIES,
    strategyFor,
    TOP_UPS,
    type Step,
} from "./setup-steps";

type Profiles =
    | { kind: "loading" }
    | { kind: "failed" }
    | { kind: "ready"; profiles: readonly AdvertiserProfile[] };

function useProfiles(): Profiles {
    // Open to everyone for the demo: no key needed (app/api/demo_controls.py).
    const [state, setState] = useState<Profiles>({ kind: "loading" });
    useEffect(() => {
        const controller = new AbortController();
        fetchProfiles(controller.signal)
            .then((data) => {
                setState({ kind: "ready", profiles: data.profiles });
            })
            .catch(() => {
                if (controller.signal.aborted) return;
                setState({ kind: "failed" });
            });
        return () => {
            controller.abort();
        };
    }, []);
    return state;
}

/** Keep `?brand=…&step=…` in the URL so a reload or a shared link resumes the setup. */
function useSetupLocation(): [string | null, Step, (brand: string | null, step: Step) => void] {
    const read = () => new URLSearchParams(window.location.search);
    const [brand, setBrand] = useState<string | null>(() => read().get("brand"));
    const [step, setStep] = useState<Step>(() =>
        read().get("brand") ? parseStep(read().get("step")) : "brand",
    );
    const go = (nextBrand: string | null, next: Step) => {
        setBrand(nextBrand);
        setStep(next);
        const params = new URLSearchParams();
        if (nextBrand) params.set("brand", nextBrand);
        if (nextBrand && next !== "brand") params.set("step", next);
        const query = params.toString();
        window.history.replaceState(
            null,
            "",
            `${window.location.pathname}${query ? `?${query}` : ""}`,
        );
    };
    return [brand, step, go];
}

/** `/advertisers`: a brand sets itself up to bid, in five short steps. */
export function SetupPage() {
    const profiles = useProfiles();
    const { book, error } = useBook(2000);
    const [brand, step, go] = useSetupLocation();
    const profile =
        profiles.kind === "ready"
            ? (profiles.profiles.find((p) => p.brand_name === brand) ?? null)
            : null;
    const line = book?.lines.find((l) => l.campaign_id === profile?.campaign_id) ?? null;
    const current = profile ? step : "brand";

    return (
        <AppShell>
            <PageHeader
                title="Anunciantes"
                description="Configura tu marca para pujar por momentos en las pantallas."
                actions={
                    profile && (
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                                go(null, "brand");
                            }}
                        >
                            Cambiar marca
                        </Button>
                    )
                }
            />
            {profiles.kind === "failed" ? (
                <Card>
                    <EmptyState
                        title="No se pudo cargar el catálogo"
                        description="Revisa la conexión con la estación y recarga la página."
                    />
                </Card>
            ) : (
                <>
                    <Stepper current={current} />
                    {error && (
                        <Badge tone="danger" appearance="soft">
                            Sin conexión con el mercado
                        </Badge>
                    )}
                    {profiles.kind === "loading" ? (
                        <Card className="p-6 text-compact text-muted">Cargando marcas…</Card>
                    ) : !profile ? (
                        <BrandStep
                            profiles={profiles.profiles}
                            lines={book?.lines ?? []}
                            onPick={(name) => {
                                go(name, "audience");
                            }}
                        />
                    ) : (
                        <>
                            <BrandSummary profile={profile} line={line} />
                            {current === "audience" && <AudienceStep profile={profile} />}
                            {current === "budget" && <BudgetStep line={line} />}
                            {current === "bid" && <BidStep profile={profile} line={line} />}
                            {current === "live" && <LiveStep profile={profile} line={line} />}
                            <StepNav
                                step={current}
                                onBack={() => {
                                    const back = previousStep(current);
                                    go(back === "brand" ? null : brand, back);
                                }}
                                onNext={() => {
                                    go(brand, nextStep(current));
                                }}
                            />
                        </>
                    )}
                </>
            )}
        </AppShell>
    );
}

function Stepper({ current }: { current: Step }) {
    const at = stepIndex(current);
    return (
        <ol aria-label="Pasos" className="flex flex-wrap gap-2">
            {STEPS.map((s, index) => {
                const done = index < at;
                const active = index === at;
                return (
                    <li
                        key={s.key}
                        aria-current={active ? "step" : undefined}
                        className={
                            active
                                ? "flex items-center gap-2 rounded-control bg-surface-raised px-3 py-1.5 text-compact font-medium"
                                : "flex items-center gap-2 px-3 py-1.5 text-compact text-muted"
                        }
                    >
                        <span
                            aria-hidden
                            className="grid size-5 place-items-center rounded-full border border-border text-metadata tabular-nums"
                        >
                            {done ? <Check className="size-3" /> : index + 1}
                        </span>
                        {s.label}
                        {done && <span className="sr-only">(listo)</span>}
                    </li>
                );
            })}
        </ol>
    );
}

function StepNav({ step, onBack, onNext }: { step: Step; onBack: () => void; onNext: () => void }) {
    const last = step === "live";
    return (
        <div className="flex items-center justify-between gap-3">
            <Button variant="ghost" onClick={onBack}>
                <ArrowLeft className="size-4" aria-hidden />
                Volver
            </Button>
            {!last && (
                <Button variant="primary" onClick={onNext}>
                    Continuar
                    <ArrowRight className="size-4" aria-hidden />
                </Button>
            )}
        </div>
    );
}

function Logo({ profile, size = "md" }: { profile: AdvertiserProfile; size?: "md" | "lg" }) {
    return (
        <span
            className={
                size === "lg"
                    ? "flex h-14 w-24 shrink-0 items-center justify-center overflow-hidden rounded-control border border-border-subtle bg-surface"
                    : "flex h-10 w-16 shrink-0 items-center justify-center overflow-hidden rounded-control border border-border-subtle bg-surface"
            }
        >
            <img
                src={assetUrl(profile.logo_url)}
                alt=""
                className="block h-full w-full object-contain p-1"
            />
        </span>
    );
}

function BrandStep({
    profiles,
    lines,
    onPick,
}: {
    profiles: readonly AdvertiserProfile[];
    lines: readonly BookLine[];
    onPick: (brand: string) => void;
}) {
    if (profiles.length === 0) {
        return (
            <Card>
                <EmptyState
                    title="Sin marcas todavía"
                    description="Aparecen cuando el mercado publica su catálogo aprobado."
                />
            </Card>
        );
    }
    return (
        <section aria-labelledby="pick" className="flex flex-col gap-3">
            <h2 id="pick" className="text-compact text-muted">
                Elige la marca que vas a configurar.
            </h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {profiles.map((p) => {
                    const onAir = lines.find((l) => l.campaign_id === p.campaign_id)?.on_air;
                    return (
                        <button
                            key={p.campaign_id}
                            type="button"
                            onClick={() => {
                                onPick(p.brand_name);
                            }}
                            className="flex items-center gap-3 rounded-container border border-border bg-surface p-4 text-left transition-colors hover:bg-surface-hover"
                        >
                            <Logo profile={p} />
                            <span className="min-w-0">
                                <span className="block font-medium">{p.brand_name}</span>
                                <span className="block text-compact text-muted">
                                    {categoryLabel(p.category)}
                                </span>
                            </span>
                            {onAir && (
                                <Badge tone="success" className="ml-auto">
                                    Al aire
                                </Badge>
                            )}
                        </button>
                    );
                })}
            </div>
        </section>
    );
}

function BrandSummary({ profile, line }: { profile: AdvertiserProfile; line: BookLine | null }) {
    return (
        <Card className="flex flex-wrap items-center gap-4 p-4">
            <Logo profile={profile} size="lg" />
            <div className="min-w-0 flex-1">
                <p className="text-body font-semibold">{profile.brand_name}</p>
                <p className="text-compact text-muted">
                    {categoryLabel(profile.category)}
                    {profile.tagline ? ` · ${profile.tagline}` : ""}
                </p>
            </div>
            {line && (
                <dl className="flex gap-6 text-compact">
                    <div>
                        <dt className="text-muted">Disponible</dt>
                        <dd className="font-medium tabular-nums">
                            {moneyCl(line.remaining.amount)}
                        </dd>
                    </div>
                    <div>
                        <dt className="text-muted">Puja</dt>
                        <dd className="font-medium tabular-nums">
                            ×{decimalCl(line.multiplier, 1)}
                        </dd>
                    </div>
                    <div>
                        <dt className="text-muted">Estado</dt>
                        <dd>
                            {line.on_air ? (
                                <Badge tone="success">Al aire</Badge>
                            ) : (
                                <Badge>En espera</Badge>
                            )}
                        </dd>
                    </div>
                </dl>
            )}
        </Card>
    );
}

function OfferList({ offers }: { offers: readonly AdvertiserOffer[] }) {
    return (
        <ul className="flex flex-col gap-1 text-compact">
            {offers.map((o) => (
                <li key={o.min_party} className="flex items-baseline justify-between gap-3">
                    <span className="min-w-0">
                        <span className="font-medium">{o.label}</span>{" "}
                        <span className="text-muted">
                            · desde {o.min_party} {o.min_party === 1 ? "persona" : "personas"}
                        </span>
                    </span>
                    <span className="shrink-0 font-medium tabular-nums">
                        {moneyCl(o.price.amount)}
                    </span>
                </li>
            ))}
        </ul>
    );
}

function AudienceStep({ profile }: { profile: AdvertiserProfile }) {
    return (
        <div className="grid gap-4 lg:grid-cols-2">
            <Card className="flex flex-col gap-4 p-4">
                <header className="flex items-center gap-2">
                    <Users className="size-4 text-muted" aria-hidden />
                    <h2 className="font-medium">A quién le hablas</h2>
                </header>
                <ul className="flex flex-col divide-y divide-border-subtle">
                    {profile.audiences.map((a) => (
                        <li
                            key={a.segment_id}
                            className="flex items-baseline justify-between gap-3 py-2"
                        >
                            <span className="text-compact">{a.label}</span>
                            <span className="shrink-0 text-compact tabular-nums">
                                {moneyCl(a.rate_per_attention_s.amount)}{" "}
                                <span className="text-muted">/ seg. de atención</span>
                            </span>
                        </li>
                    ))}
                </ul>
                <p className="text-metadata text-muted">
                    {profile.broad_match
                        ? "También llegas a audiencias relacionadas, con descuento."
                        : "Solo pujas por estas audiencias exactas."}{" "}
                    Si nadie mira la pantalla, no pagas.
                </p>
                <p className="flex items-start gap-2 text-metadata text-muted">
                    <ShieldCheck className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                    {profile.age_restricted
                        ? "Categoría restringida: tu aviso se bloquea si hay menores frente a la pantalla."
                        : "Sin datos personales: la pantalla solo ve grupos anónimos."}
                </p>
            </Card>
            <Card className="flex flex-col gap-4 p-4">
                <header className="flex items-center gap-2">
                    <ShoppingBasket className="size-4 text-muted" aria-hidden />
                    <h2 className="font-medium">Canastas con partners</h2>
                </header>
                {profile.partners.length === 0 ? (
                    <p className="text-compact text-muted">
                        Aún no tienes partners aprobados: apareces solo con tu propia oferta.
                    </p>
                ) : (
                    <div className="flex flex-col gap-4">
                        {profile.partners.map((partner) => (
                            <div key={partner.brand_name} className="flex flex-col gap-2">
                                <p className="text-compact font-medium">
                                    {profile.brand_name} + {partner.brand_name}
                                </p>
                                {partner.offers.length > 0 ? (
                                    <OfferList offers={partner.offers} />
                                ) : (
                                    <p className="text-metadata text-muted">
                                        Sin oferta conjunta aprobada.
                                    </p>
                                )}
                            </div>
                        ))}
                    </div>
                )}
                {profile.solo_offers.length > 0 && (
                    <div className="flex flex-col gap-2 border-t border-border-subtle pt-4">
                        <p className="text-compact font-medium">Solo {profile.brand_name}</p>
                        <OfferList offers={profile.solo_offers} />
                    </div>
                )}
                <p className="text-metadata text-muted">
                    Los partners y precios están aprobados por ambas marcas. La IA escribe el texto,
                    nunca cambia un precio.
                </p>
            </Card>
            {profile.competitors.length > 0 && (
                <Card className="flex items-start gap-3 p-4 lg:col-span-2">
                    <Swords className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden />
                    <p className="text-compact">
                        Compites con{" "}
                        <span className="font-medium">{profile.competitors.join(", ")}</span>.
                        <span className="text-muted"> Nunca comparten canasta contigo.</span>
                    </p>
                </Card>
            )}
        </div>
    );
}

type TopUpState =
    | { kind: "idle" }
    | { kind: "sending"; amount: number; key: string }
    | { kind: "done"; amount: number }
    | { kind: "failed"; amount: number; key: string };

function newKey(): string {
    return crypto.randomUUID().replaceAll("-", "");
}

function BudgetStep({ line }: { line: BookLine | null }) {
    const [custom, setCustom] = useState("");
    const [state, setState] = useState<TopUpState>({ kind: "idle" });
    // A retry reuses the failed request's key: the backend applies a top-up once.
    const send = (amount: number, key: string) => {
        if (!line) return;
        setState({ kind: "sending", amount, key });
        topUpBudget(line.campaign_id, amount, key)
            .then(() => {
                setState({ kind: "done", amount });
                setCustom("");
            })
            .catch(() => {
                setState({ kind: "failed", amount, key });
            });
    };
    const typed = parseTopUp(custom);
    const busy = state.kind === "sending";
    return (
        <Card className="flex flex-col gap-6 p-4">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                <Metric
                    label="Presupuesto total"
                    value={line ? moneyCl(line.budget.amount) : "—"}
                />
                <Metric label="Gastado" value={line ? moneyCl(line.spent.amount) : "—"} />
                <Metric
                    label="Disponible"
                    value={line ? moneyCl(line.remaining.amount) : "—"}
                    hint="Cobramos solo por segundos de atención medidos."
                />
            </div>
            <div className="flex flex-col gap-3 border-t border-border-subtle pt-4">
                <p className="text-compact font-medium">Agregar presupuesto</p>
                <div className="flex flex-wrap gap-2">
                    {TOP_UPS.map((amount) => (
                        <Button
                            key={amount}
                            disabled={!line || busy}
                            onClick={() => {
                                send(amount, newKey());
                            }}
                        >
                            +{moneyCl(String(amount))}
                        </Button>
                    ))}
                </div>
                <form
                    className="flex flex-wrap items-end gap-2"
                    onSubmit={(e) => {
                        e.preventDefault();
                        if (typed !== null) send(typed, newKey());
                    }}
                >
                    <label className="flex flex-col gap-1 text-compact">
                        <span className="text-muted">Otro monto (CLP)</span>
                        <input
                            inputMode="numeric"
                            value={custom}
                            onChange={(e) => {
                                setCustom(e.target.value);
                            }}
                            placeholder="25.000"
                            className="h-10 w-40 rounded-control border border-control-edge bg-surface px-3 tabular-nums"
                        />
                    </label>
                    <Button type="submit" disabled={!line || busy || typed === null} loading={busy}>
                        Agregar
                    </Button>
                </form>
                <div role="status" aria-live="polite" className="text-compact">
                    {state.kind === "done" && (
                        <Badge tone="success" appearance="soft">
                            Agregaste {moneyCl(String(state.amount))}
                        </Badge>
                    )}
                    {state.kind === "failed" && (
                        <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                                send(state.amount, state.key);
                            }}
                        >
                            No se agregó · Reintentar {moneyCl(String(state.amount))}
                        </Button>
                    )}
                    {custom !== "" && typed === null && (
                        <span className="text-metadata text-warning">
                            Escribe un monto entero entre $1 y $10.000.000.
                        </span>
                    )}
                </div>
            </div>
        </Card>
    );
}

function BidStep({ profile, line }: { profile: AdvertiserProfile; line: BookLine | null }) {
    const [pending, setPending] = useState<string | null>(null);
    const [failed, setFailed] = useState<string | null>(null);
    const active = line ? strategyFor(line.multiplier) : null;
    const pick = (multiplier: string) => {
        if (!line) return;
        setPending(multiplier);
        setFailed(null);
        setBidMultiplier(line.campaign_id, multiplier, line.multiplier_seq)
            .catch(() => {
                setFailed(multiplier);
            })
            .finally(() => {
                setPending(null);
            });
    };
    return (
        <div className="flex flex-col gap-4">
            <div className="grid gap-3 sm:grid-cols-2">
                {STRATEGIES.map((s) => {
                    const selected = active?.key === s.key && !line?.autobid;
                    return (
                        <button
                            key={s.key}
                            type="button"
                            aria-pressed={selected}
                            disabled={!line || pending !== null}
                            onClick={() => {
                                pick(s.multiplier);
                            }}
                            className={
                                selected
                                    ? "flex flex-col gap-2 rounded-container border-2 border-primary bg-surface p-4 text-left"
                                    : "flex flex-col gap-2 rounded-container border border-border bg-surface p-4 text-left transition-colors hover:bg-surface-hover disabled:opacity-50"
                            }
                        >
                            <span className="flex items-center justify-between gap-2">
                                <span className="font-medium">{s.title}</span>
                                <span className="text-compact text-muted tabular-nums">
                                    ×{decimalCl(s.multiplier, 1)}
                                </span>
                            </span>
                            <span className="text-compact text-muted">{s.description}</span>
                            <span className="text-metadata text-muted tabular-nums">
                                {profile.audiences
                                    .slice(0, 2)
                                    .map(
                                        (a) =>
                                            `hasta ${moneyCl(maxRate(a.rate_per_attention_s.amount, s.multiplier))}/seg.`,
                                    )
                                    .join(" · ")}
                            </span>
                            {selected && (
                                <Badge tone="success" appearance="soft" className="self-start">
                                    Activa
                                </Badge>
                            )}
                        </button>
                    );
                })}
            </div>
            <Card className="flex flex-wrap items-center justify-between gap-3 p-4">
                <div className="min-w-0">
                    <p className="font-medium">Puja automática</p>
                    <p className="text-compact text-muted">
                        {line?.autobid && line.autobid_reason
                            ? line.autobid_reason
                            : "Ajusta tu puja sola según atención y canjes. Mover una estrategia la apaga."}
                    </p>
                </div>
                <Button
                    variant={line?.autobid ? "primary" : "outline"}
                    aria-pressed={line?.autobid ?? false}
                    disabled={!line}
                    onClick={() => {
                        if (!line) return;
                        setFailed(null);
                        setAutoBid(line.campaign_id, !line.autobid).catch(() => {
                            setFailed("auto");
                        });
                    }}
                >
                    {line?.autobid ? "Activa" : "Activar"}
                </Button>
            </Card>
            <div role="status" aria-live="polite">
                {pending !== null && <Badge appearance="soft">Enviando puja…</Badge>}
                {failed !== null && (
                    <Badge tone="warning" appearance="soft">
                        No se pudo cambiar la puja. Inténtalo de nuevo.
                    </Badge>
                )}
                {line && !active && !line.autobid && (
                    <p className="text-metadata text-muted">
                        Tu puja actual es ×{decimalCl(line.multiplier, 1)}, ajustada a mano.
                    </p>
                )}
            </div>
        </div>
    );
}

function LiveStep({ profile, line }: { profile: AdvertiserProfile; line: BookLine | null }) {
    const key = demoKey() ?? rememberedDemoKey();
    const ready = line !== null && Number(line.remaining.amount) > 0 && line.in_stock;
    const checks: readonly (readonly [boolean, string, string])[] = [
        [
            profile.audiences.length > 0,
            "Audiencias",
            profile.audiences.length === 1
                ? "1 audiencia con tarifa"
                : `${profile.audiences.length} audiencias con tarifa`,
        ],
        [
            line !== null && Number(line.remaining.amount) > 0,
            "Presupuesto",
            line ? `${moneyCl(line.remaining.amount)} disponible` : "Sin datos",
        ],
        [
            line !== null,
            "Puja",
            line
                ? line.autobid
                    ? "Automática"
                    : `${strategyFor(line.multiplier)?.title ?? "Manual"} · ×${decimalCl(line.multiplier, 1)}`
                : "Sin datos",
        ],
        [
            line?.in_stock ?? false,
            "Stock",
            line?.in_stock ? "Disponible en la tienda" : "Sin stock en la tienda",
        ],
    ];
    return (
        <Card className="flex flex-col gap-6 p-4">
            <div className="flex flex-wrap items-center gap-3">
                <p className="text-body font-semibold">
                    {ready ? "Tu marca está pujando" : "Falta un paso para pujar"}
                </p>
                {line?.on_air ? (
                    <Badge tone="success" appearance="soft">
                        Al aire ahora
                    </Badge>
                ) : ready ? (
                    <Badge tone="info" appearance="soft">
                        Esperando un momento
                    </Badge>
                ) : (
                    <Badge tone="warning" appearance="soft">
                        Revisa tu configuración
                    </Badge>
                )}
            </div>
            <ul className="flex flex-col divide-y divide-border-subtle">
                {checks.map(([ok, label, detail]) => (
                    <li
                        key={label}
                        className="flex items-center justify-between gap-3 py-2 text-compact"
                    >
                        <span className="flex items-center gap-2">
                            {ok ? (
                                <Badge tone="success">{label}</Badge>
                            ) : (
                                <Badge tone="warning">{label}</Badge>
                            )}
                        </span>
                        <span className="text-muted tabular-nums">{detail}</span>
                    </li>
                ))}
            </ul>
            <p className="text-compact text-muted">
                Ganas cuando el grupo frente a la pantalla calza con tus audiencias. Pagas por
                segundo de atención medido, y cada canje del QR se acredita a todas las marcas de la
                canasta.
            </p>
            <div className="flex flex-wrap gap-2">
                {key !== null && (
                    <Button asChild variant="primary">
                        <a
                            href={demoHref(
                                key,
                                "advertiser",
                                `?brand=${encodeURIComponent(profile.brand_name)}`,
                            )}
                        >
                            Pujar en vivo
                        </a>
                    </Button>
                )}
                <Button asChild>
                    <a href="/">Ver consola</a>
                </Button>
            </div>
        </Card>
    );
}
