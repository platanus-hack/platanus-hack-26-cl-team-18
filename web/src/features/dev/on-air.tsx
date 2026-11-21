import type { ConsoleAuction, DevCopy, DevCreative, DevPlayer } from "@/contract";
import { ms } from "@/features/console";
import { CreativeView } from "@/features/screen";
import { moneyCl } from "@/lib/format";
import { Badge } from "@/shared/ui/badge";
import { Panel } from "@/shared/ui/Panel";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/shared/ui/table";
import { buildTrace, type DevScreen } from "./dev-state";
import { clock, toneOf } from "./format";

const STEP: Record<DevCreative["step"], string> = {
    composed: "Compuesta",
    house: "Aviso de la casa",
    copy_attempt: "Intento de texto IA",
    copy_upgraded: "Texto IA al aire",
    voice_upgraded: "Voz al aire",
};

const PLAYER: Record<DevPlayer["event"], string> = {
    displayed: "Visible en pantalla",
    playback: "Reproducción terminada",
    voice: "Voz",
};

function CopyDraft({ draft }: { draft: DevCopy }) {
    return (
        <div className="flex flex-col gap-1 rounded-control border border-border-subtle p-2">
            <div className="flex flex-wrap items-center gap-2">
                <Badge tone={draft.source === "ai" ? "info" : "neutral"}>{draft.source}</Badge>
                {draft.score !== null && (
                    <span className="tabular-nums">puntaje {draft.score}</span>
                )}
                {draft.passed ? (
                    <Badge tone="success">Pasa el guardia</Badge>
                ) : (
                    <Badge tone="danger">No pasa el guardia</Badge>
                )}
                {draft.model_version && <span className="text-muted">{draft.model_version}</span>}
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
                <dt className="text-muted">Gancho</dt>
                <dd>{draft.teaser}</dd>
                <dt className="text-muted">Oferta</dt>
                <dd>{draft.offer}</dd>
                <dt className="text-muted">Cierre</dt>
                <dd>{draft.close}</dd>
                <dt className="text-muted">Voz</dt>
                <dd>{draft.voice_line}</dd>
            </dl>
            {draft.violations.length > 0 && <Violations items={draft.violations} />}
        </div>
    );
}

function Violations({ items }: { items: DevCreative["violations"] }) {
    return (
        <ul className="flex flex-wrap gap-1" aria-label="Infracciones">
            {items.map((v, i) => (
                <li key={`${v.kind}:${v.where}:${String(i)}`} title={v.detail || undefined}>
                    <Badge tone="warning">
                        {v.kind} · {v.where}
                    </Badge>
                </li>
            ))}
        </ul>
    );
}

function Step({ step }: { step: DevCreative }) {
    const brief = step.brief;
    return (
        <li className="flex flex-col gap-1.5 border-l-2 border-border-strong pl-3 text-compact">
            <div className="flex flex-wrap items-center gap-2 tabular-nums">
                <span className="font-medium">{STEP[step.step]}</span>
                {step.outcome && <Badge tone={toneOf(step.outcome)}>{step.outcome}</Badge>}
                {step.latency_ms !== null && <span>{ms(step.latency_ms)}</span>}
                {step.compose_ms !== null && (
                    <span className="text-muted">compone {ms(step.compose_ms)}</span>
                )}
                <span className="text-muted">{clock(step.at)}</span>
            </div>
            {brief && (
                <p className="text-muted">
                    {brief.brands.join(" + ")} · {brief.layout} · grupo {brief.party}/{brief.people}{" "}
                    · ángulo {brief.angle} ({brief.angle_reason})
                    {brief.offer_label && ` · ${brief.offer_label}`}
                    {brief.offer_price && ` ${moneyCl(brief.offer_price.amount)}`}
                    {brief.held_brand && ` · en mano ${brief.held_brand}`}
                    {brief.daypart && ` · ${brief.daypart}`}
                    {brief.minor_likely && " · posible menor"}
                </p>
            )}
            {(step.ai_score !== null || step.stock_score !== null) && (
                <p className="tabular-nums">
                    IA {step.ai_score ?? "—"} vs. stock {step.stock_score ?? "—"}
                </p>
            )}
            {step.violations.length > 0 && <Violations items={step.violations} />}
            {step.copy_draft && <CopyDraft draft={step.copy_draft} />}
            {(step.voiceover_url ?? step.video_url ?? step.redemption_code) && (
                <p className="break-all text-muted">
                    {step.voiceover_url && <>voz {step.voiceover_url} </>}
                    {step.video_url && <>video {step.video_url} </>}
                    {step.redemption_code && <>código {step.redemption_code}</>}
                </p>
            )}
            {step.note && <p className="text-muted">{step.note}</p>}
        </li>
    );
}

export function OnAirPanel({ view }: { view: DevScreen }) {
    const onAir = view.onAir;
    const steps = onAir ? buildTrace(view.creatives, onAir.creative_id) : [];
    const player = onAir ? view.player.filter((e) => e.creative_id === onAir.creative_id) : [];
    return (
        <Panel title="Al aire (espejo)" aside={onAir && onAir.creative_id.slice(-8)}>
            {!onAir ? (
                <p className="text-compact text-muted">Ninguna creatividad recibida todavía.</p>
            ) : (
                <div className="flex flex-col gap-3">
                    <div
                        className="relative aspect-video overflow-hidden rounded-control bg-ink"
                        aria-label="Espejo de la pantalla: lo que debería verse"
                    >
                        <CreativeView spec={onAir.spec} playKey={onAir.creative_id} />
                    </div>
                    <p className="text-metadata text-muted">
                        Espejo: lo que la pantalla debería mostrar. No reporta reproducción.
                    </p>
                    <div className="flex flex-wrap gap-2">
                        <Badge appearance="soft">{onAir.type}</Badge>
                        {onAir.spec.copy_source && (
                            <Badge tone="info" appearance="soft">
                                Texto {onAir.spec.copy_source}
                            </Badge>
                        )}
                        {onAir.spec.angle && (
                            <Badge appearance="soft">Ángulo {onAir.spec.angle}</Badge>
                        )}
                        {onAir.spec.offer && (
                            <Badge tone="success" appearance="soft">
                                {onAir.spec.offer.label} · {moneyCl(onAir.spec.offer.price.amount)}
                            </Badge>
                        )}
                        {onAir.spec.voiceover_url && (
                            <Badge tone="success" appearance="soft">
                                Con voz
                            </Badge>
                        )}
                        {onAir.spec.video_url && (
                            <Badge tone="success" appearance="soft">
                                Con video
                            </Badge>
                        )}
                    </div>
                    <h3 className="text-compact font-semibold">Cómo se construyó</h3>
                    {steps.length === 0 ? (
                        <p className="text-compact text-muted">
                            Sin pasos de construcción para esta creatividad.
                        </p>
                    ) : (
                        <ol className="flex flex-col gap-3">
                            {steps.map((s, i) => (
                                <Step key={`${s.at}:${s.step}:${String(i)}`} step={s} />
                            ))}
                        </ol>
                    )}
                    <h3 className="text-compact font-semibold">Lo que reportó la pantalla</h3>
                    {player.length === 0 ? (
                        <p className="text-compact text-muted">Sin reportes de la pantalla.</p>
                    ) : (
                        <ul className="flex flex-col gap-1 text-compact tabular-nums">
                            {player.map((e, i) => (
                                <li key={`${e.at}:${e.event}:${String(i)}`}>
                                    {clock(e.at)} · {PLAYER[e.event]}
                                    {e.played_s !== null && ` · ${e.played_s.toFixed(1)} s`}
                                    {e.voice_playing !== null &&
                                        (e.voice_playing ? " · sonando" : " · detenida")}
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}
        </Panel>
    );
}

export function AuctionHistory({ auctions }: { auctions: readonly ConsoleAuction[] }) {
    return (
        <Panel title="Historial de subastas" aside={`${auctions.length} últimas`}>
            {auctions.length === 0 ? (
                <p className="text-compact text-muted">Sin subastas todavía.</p>
            ) : (
                <Table aria-label="Historial de subastas">
                    <TableHeader>
                        <TableRow>
                            <TableHead>Hora</TableHead>
                            <TableHead>Ganadores</TableHead>
                            <TableHead>Oferta</TableHead>
                            <TableHead>Causa</TableHead>
                            <TableHead>Cambio</TableHead>
                            <TableHead className="text-right">Excl.</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {auctions.map((a) => (
                            <TableRow key={a.auction_id}>
                                <TableCell>
                                    {a.trace.cleared_at ? clock(a.trace.cleared_at) : "—"}
                                </TableCell>
                                <TableCell>
                                    {a.winners.map((w) => w.brand_name).join(" + ") || "Casa"}
                                </TableCell>
                                <TableCell>
                                    {a.offer
                                        ? `${a.offer.label} ${moneyCl(a.offer.price.amount)}`
                                        : "—"}
                                </TableCell>
                                <TableCell>{a.trigger ?? "—"}</TableCell>
                                <TableCell>{a.swap ?? "—"}</TableCell>
                                <TableCell
                                    className="text-right"
                                    title={a.exclusions
                                        .map((e) => `${e.reason}${e.detail ? `: ${e.detail}` : ""}`)
                                        .join("\n")}
                                >
                                    {a.exclusions.length}
                                </TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            )}
        </Panel>
    );
}
