import type { DevBox, DevPerception, DevSpeech, DevVlm } from "@/contract";
import { ms } from "@/features/console";
import { Badge } from "@/shared/ui/badge";
import { Panel } from "@/shared/ui/Panel";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/shared/ui/table";
import { KEYFRAME_TTL_MS, type DevScreen, type PerceptionSample } from "./dev-state";
import { clock, DISTANCE, pct, seconds, toneOf } from "./format";

const Empty = ({ children }: { children: string }) => (
    <p className="text-compact text-muted">{children}</p>
);

function rect(box: DevBox, w: number, h: number) {
    return {
        x: box.x1 * w,
        y: box.y1 * h,
        width: Math.max(0, (box.x2 - box.x1) * w),
        height: Math.max(0, (box.y2 - box.y1) * h),
    };
}

/** Frames per second over the last two seconds of samples (device clock). */
export function sampleRate(history: readonly PerceptionSample[]): number | null {
    const newest = history[0];
    if (!newest) return null;
    const recent = history.filter((s) => newest.atMs - s.atMs <= 2000);
    const oldest = recent.at(-1);
    if (!oldest || recent.length < 2 || newest.atMs === oldest.atMs) return null;
    return ((recent.length - 1) * 1000) / (newest.atMs - oldest.atMs);
}

function Sparkline({ values, label }: { values: readonly number[]; label: string }) {
    if (values.length < 2) return null;
    const max = Math.max(...values, 1);
    const points = values
        .slice()
        .reverse()
        .map((v, i, all) => `${(i / (all.length - 1)) * 100},${30 - (v / max) * 28}`)
        .join(" ");
    return (
        <svg
            viewBox="0 0 100 30"
            preserveAspectRatio="none"
            className="h-8 w-full text-accent"
            role="img"
            aria-label={label}
        >
            <polyline
                points={points}
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                vectorEffect="non-scaling-stroke"
            />
        </svg>
    );
}

/** Boxes over the blurred keyframe when one is fresh, over a schematic frame otherwise. */
export function BoxCanvas({
    perception,
    keyframe,
    nowMs,
}: {
    perception: DevPerception;
    keyframe: DevScreen["keyframe"];
    nowMs: number;
}) {
    const w = perception.frame_w;
    const h = perception.frame_h;
    const image =
        keyframe && nowMs - keyframe.receivedAtMs <= KEYFRAME_TTL_MS
            ? `data:image/jpeg;base64,${keyframe.frame.jpeg_base64}`
            : null;
    const stroke = Math.max(2, w / 320);
    const font = Math.max(12, w / 48);
    return (
        <svg
            viewBox={`0 0 ${w} ${h}`}
            className="w-full rounded-control border border-border-subtle bg-surface-sunken"
            role="img"
            aria-label={`${perception.people.length} personas y ${perception.objects.length} objetos detectados`}
        >
            {image ? (
                <image href={image} x={0} y={0} width={w} height={h} opacity={0.85} />
            ) : (
                <g stroke="var(--color-border)" strokeWidth={stroke / 2}>
                    <line x1={w / 3} y1={0} x2={w / 3} y2={h} />
                    <line x1={(2 * w) / 3} y1={0} x2={(2 * w) / 3} y2={h} />
                    <line x1={0} y1={h / 2} x2={w} y2={h / 2} />
                </g>
            )}
            {perception.objects.map((o, i) => {
                const r = rect(o.box, w, h);
                const color = o.near_person ? "var(--color-warning)" : "var(--color-muted)";
                return (
                    <g key={`o${String(i)}`}>
                        <rect
                            {...r}
                            fill="none"
                            stroke={color}
                            strokeWidth={stroke}
                            strokeDasharray={`${String(stroke * 3)} ${String(stroke * 2)}`}
                        />
                        <text x={r.x} y={r.y + r.height + font} fontSize={font} fill={color}>
                            {o.label} {pct(o.confidence)}
                            {o.near_person ? " · en mano" : ""}
                        </text>
                    </g>
                );
            })}
            {perception.markers.map((m) => {
                const r = rect(m.box, w, h);
                return (
                    <g key={`m${String(m.marker_id)}`}>
                        <rect {...r} fill="none" stroke="var(--color-info)" strokeWidth={stroke} />
                        <text x={r.x} y={r.y - font / 3} fontSize={font} fill="var(--color-info)">
                            marcador {m.marker_id}
                            {m.brand_ref ? ` · ${m.brand_ref}` : ""}
                        </text>
                    </g>
                );
            })}
            {perception.people.map((p) => {
                const r = rect(p.box, w, h);
                const color = p.looking ? "var(--color-accent)" : "var(--color-fg)";
                return (
                    <g key={p.track_id}>
                        <rect {...r} fill="none" stroke={color} strokeWidth={stroke * 1.5} />
                        <rect
                            x={r.x}
                            y={Math.max(0, r.y - font * 1.4)}
                            width={font * 11}
                            height={font * 1.4}
                            fill={color}
                        />
                        <text
                            x={r.x + font / 3}
                            y={Math.max(font, r.y - font / 3)}
                            fontSize={font}
                            fill="var(--color-surface)"
                        >
                            {p.track_id} {p.looking ? "▲ mira" : "· no mira"} ·{" "}
                            {DISTANCE[p.distance_band]}
                        </text>
                    </g>
                );
            })}
        </svg>
    );
}

export function PerceptionPanel({ view, nowMs }: { view: DevScreen; nowMs: number }) {
    const p = view.perception;
    const fps = sampleRate(view.perceptionHistory);
    const age = p ? nowMs - Date.parse(p.measured_at) : null;
    return (
        <Panel
            title="Percepción (YOLO)"
            aside={p && `${p.detector} · ${fps === null ? "—" : fps.toFixed(1)} fps`}
        >
            {!p ? (
                <Empty>Sin cuadros del agente. Inicia el agente de visión con el stream dev.</Empty>
            ) : (
                <div className="flex flex-col gap-3">
                    <BoxCanvas perception={p} keyframe={view.keyframe} nowMs={nowMs} />
                    <div className="flex flex-wrap gap-2">
                        {age !== null && age > 2000 && (
                            <Badge tone="warning" appearance="soft">
                                Último cuadro hace {seconds(age)}
                            </Badge>
                        )}
                        {view.keyframe && nowMs - view.keyframe.receivedAtMs <= KEYFRAME_TTL_MS && (
                            <Badge tone="info" appearance="soft">
                                Fondo: cuadro difuminado
                            </Badge>
                        )}
                        {p.held.map((brand) => (
                            <Badge key={brand} tone="success" appearance="soft">
                                En mano · {brand}
                            </Badge>
                        ))}
                    </div>
                    <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-compact tabular-nums">
                        <dt className="text-muted">Inferencia</dt>
                        <dd>{ms(p.inference_ms)}</dd>
                        <dt className="text-muted">Ida y vuelta GPU</dt>
                        <dd>{ms(p.round_trip_ms)}</dd>
                        <dt className="text-muted">Personas (crudas / seguidas)</dt>
                        <dd>
                            {p.raw_people} / {p.people.length}
                        </dd>
                        <dt className="text-muted">Cuadros (viejos)</dt>
                        <dd>
                            {p.frames} ({p.stale_frames})
                        </dd>
                    </dl>
                    <Sparkline
                        values={view.perceptionHistory.map((s) => s.inferenceMs)}
                        label="Inferencia por cuadro"
                    />
                    {p.people.length > 0 && (
                        <Table aria-label="Personas seguidas">
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Track</TableHead>
                                    <TableHead>Mira</TableHead>
                                    <TableHead>Distancia</TableHead>
                                    <TableHead className="text-right">Permanencia</TableHead>
                                    <TableHead className="text-right">Conf.</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {p.people.map((t) => (
                                    <TableRow key={t.track_id}>
                                        <TableCell className="font-mono">{t.track_id}</TableCell>
                                        <TableCell>
                                            {t.looking ? (
                                                <Badge tone="success">Sí</Badge>
                                            ) : (
                                                <Badge>No</Badge>
                                            )}
                                        </TableCell>
                                        <TableCell>{DISTANCE[t.distance_band]}</TableCell>
                                        <TableCell className="text-right">
                                            {seconds(t.dwell_s * 1000)}
                                        </TableCell>
                                        <TableCell className="text-right">
                                            {pct(t.confidence)}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                </div>
            )}
        </Panel>
    );
}

function VlmCall({ call }: { call: DevVlm }) {
    return (
        <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2 text-compact tabular-nums">
                <Badge tone={toneOf(call.outcome)} appearance="soft">
                    {call.outcome}
                </Badge>
                <span>{ms(call.latency_ms)}</span>
                <span className="text-muted">{call.model_version || "modelo ?"}</span>
                <span className="text-muted">{clock(call.finished_at)}</span>
            </div>
            {call.error_type && (
                <p className="text-compact text-danger">Error: {call.error_type}</p>
            )}
            {call.segments.length > 0 && (
                <ul className="flex flex-col gap-1 text-compact" aria-label="Segmentos del VLM">
                    {call.segments.map((s) => (
                        <li key={s.segment_id} className="flex flex-wrap justify-between gap-2">
                            <span>
                                {s.label || s.segment_id}{" "}
                                <span className="text-muted">
                                    ({s.segment_id} · {s.viewer_count} pers.)
                                </span>
                            </span>
                            <span className="flex items-center gap-2 tabular-nums">
                                {pct(s.confidence)}
                                {s.kept ? (
                                    <Badge tone="success">Usado</Badge>
                                ) : (
                                    <Badge tone="warning">
                                        Descartado · {s.drop_reason ?? "?"}
                                    </Badge>
                                )}
                            </span>
                        </li>
                    ))}
                </ul>
            )}
            {call.held_products.length > 0 && (
                <ul className="flex flex-col gap-1 text-compact" aria-label="Productos en mano">
                    {call.held_products.map((h) => (
                        <li
                            key={`${h.category}:${h.brand_ref ?? ""}`}
                            className="flex flex-wrap justify-between gap-2"
                        >
                            <span>
                                En mano · {h.brand_ref ?? h.category}{" "}
                                <span className="text-muted">({h.category})</span>
                            </span>
                            <span className="flex items-center gap-2 tabular-nums">
                                {pct(h.confidence)}
                                {h.kept ? (
                                    <Badge tone="success">Usado</Badge>
                                ) : (
                                    <Badge tone="warning">
                                        Descartado · {h.drop_reason ?? "?"}
                                    </Badge>
                                )}
                            </span>
                        </li>
                    ))}
                </ul>
            )}
            {call.minor_likely && (
                <Badge tone="warning" appearance="soft">
                    Posible menor ({pct(call.minor_likely_confidence)})
                </Badge>
            )}
            {call.reasoning && <p className="text-compact text-muted italic">{call.reasoning}</p>}
        </div>
    );
}

export function VlmPanel({ view }: { view: DevScreen }) {
    const [latest, ...previous] = view.vlm;
    return (
        <Panel title="VLM (etiquetas de imagen)" aside={`${view.vlm.length} llamadas`}>
            {!latest ? (
                <Empty>Sin llamadas al VLM todavía.</Empty>
            ) : (
                <div className="flex flex-col gap-3">
                    <VlmCall call={latest} />
                    {previous.length > 0 && (
                        <ul
                            className="flex flex-col gap-1 border-t border-border-subtle pt-2 text-metadata text-muted tabular-nums"
                            aria-label="Llamadas anteriores"
                        >
                            {previous.slice(0, 8).map((c) => (
                                <li key={`${c.submitted_at}:${c.finished_at}`}>
                                    {clock(c.finished_at)} · {c.outcome} · {ms(c.latency_ms)} ·{" "}
                                    {c.segments.filter((s) => s.kept).length}/{c.segments.length}{" "}
                                    segmentos
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}
        </Panel>
    );
}

const STAGE: Record<DevSpeech["stage"], string> = {
    keyterms: "Palabras clave",
    extraction: "Extracción",
    echo: "Eco descartado",
};

export function SpeechPanel({ view, nowMs }: { view: DevScreen; nowMs: number }) {
    return (
        <Panel title="Voz (temas, sin texto)" aside={`${view.speech.length} eventos`}>
            {view.speech.length === 0 ? (
                <Empty>Sin eventos de voz. El modo voz está apagado o nadie habló.</Empty>
            ) : (
                <ul className="flex flex-col gap-3">
                    {view.speech.slice(0, 8).map((e, i) => (
                        <li
                            key={`${e.at}:${String(i)}`}
                            className="flex flex-col gap-1 border-b border-border-subtle pb-2 text-compact last:border-0"
                        >
                            <div className="flex flex-wrap items-center gap-2 tabular-nums">
                                <span className="font-medium">{STAGE[e.stage]}</span>
                                {e.outcome && <Badge tone={toneOf(e.outcome)}>{e.outcome}</Badge>}
                                {!e.is_final && <Badge>parcial</Badge>}
                                {e.latency_ms !== null && <span>{ms(e.latency_ms)}</span>}
                                <span className="text-muted">{clock(e.at)}</span>
                            </div>
                            {(e.wanted.length > 0 || e.rejected.length > 0) && (
                                <p className="text-muted">
                                    {e.wanted.length > 0 && <>Quiere: {e.wanted.join(", ")}</>}
                                    {e.wanted.length > 0 && e.rejected.length > 0 && " · "}
                                    {e.rejected.length > 0 && <>Rechaza: {e.rejected.join(", ")}</>}
                                </p>
                            )}
                            {e.topics.map((t) => (
                                <span key={t.segment_id} className="tabular-nums">
                                    → {t.label || t.segment_id}
                                    {t.confidence !== null && ` · ${pct(t.confidence)}`}
                                    {t.expires_at !== null &&
                                        ` · vence en ${seconds(Date.parse(t.expires_at) - nowMs)}`}
                                </span>
                            ))}
                        </li>
                    ))}
                </ul>
            )}
        </Panel>
    );
}
