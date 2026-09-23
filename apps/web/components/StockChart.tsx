"use client";

/**
 * Price history chart.
 *
 * Design decisions worth stating, because each one is a rule rather than a taste:
 *
 *   * **Price and volume never share a y-axis.** A dual-axis chart lets the
 *     author decide, by choosing scales, whether volume "confirms" a price move.
 *     Volume gets its own panel below, sharing the x-axis, so the reader draws
 *     that conclusion themselves.
 *   * **One price series, so no legend** — the title names it. A legend box for a
 *     single line is noise.
 *   * **Crosshair and tooltip ship by default.** An SVG chart in a browser is an
 *     interactive surface; a static one wastes it.
 *   * **The reference line is the intrinsic value**, drawn as a dashed rule and
 *     labelled in text, so it is legible to a colourblind reader and in print.
 *   * **Gaps are gaps.** A session with no close breaks the line rather than
 *     being bridged, because a bridged halt looks like trading that did not
 *     happen.
 */

import { useMemo, useRef, useState } from "react";
import { BarChart2 } from "lucide-react";
import type { ChartRange } from "@/lib/api";

export interface ChartCandle {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
  volume: number | null;
}

interface Props {
  symbol: string;
  candles: ChartCandle[];
  range: ChartRange;
  onRangeChange: (r: ChartRange) => void;
  currencySymbol?: string;
  intrinsicValue?: number | null;
  entryLow?: number | null;
  entryHigh?: number | null;
  loading?: boolean;
  resampled?: string | null;
  sessionsAvailable?: number;
}

const RANGES: ChartRange[] = ["1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "10Y", "MAX"];

// Virtual canvas; the SVG scales to its container via viewBox.
const W = 1000;
const PRICE_H = 300;
const VOL_H = 70;
const GAP = 14;
const PAD = { top: 16, right: 68, bottom: 26, left: 10 };

const H = PAD.top + PRICE_H + GAP + VOL_H + PAD.bottom;
const PLOT_W = W - PAD.left - PAD.right;

function scale(v: number, inMin: number, inMax: number, outMin: number, outMax: number) {
  if (inMax === inMin) return (outMin + outMax) / 2;
  return outMin + ((v - inMin) / (inMax - inMin)) * (outMax - outMin);
}

/** Axis ticks on round numbers, which are what a reader can actually hold. */
function niceTicks(min: number, max: number, count = 5): number[] {
  if (!isFinite(min) || !isFinite(max) || min === max) return [min];
  const raw = (max - min) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const start = Math.ceil(min / step) * step;
  const out: number[] = [];
  for (let v = start; v <= max + step * 0.001; v += step) out.push(v);
  return out;
}

function compact(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return v.toFixed(0);
}

export default function StockChart({
  symbol,
  candles,
  range,
  onRangeChange,
  currencySymbol = "₹",
  intrinsicValue,
  entryLow,
  entryHigh,
  loading,
  resampled,
  sessionsAvailable,
}: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);

  const model = useMemo(() => {
    const points = (candles ?? []).filter((c) => c && c.close != null);
    if (points.length < 2) return null;

    const closes = points.map((c) => c.close);
    const volumes = points.map((c) => c.volume ?? 0);

    // The price series owns the y-axis. Reference lines may stretch it, but
    // only so far.
    //
    // Letting them set the domain outright looks reasonable until a stock
    // trades at a third of its intrinsic value: the reference sits at the top,
    // the actual price history is crushed into the bottom fifth of the plot,
    // and the chart stops communicating the thing it exists to show. A
    // reference beyond the allowance is pinned to the edge and flagged, which
    // is honest — it says "off the top of this scale" rather than silently
    // rescaling everything around it.
    const priceLo = Math.min(...closes);
    const priceHi = Math.max(...closes);
    const priceSpan = priceHi - priceLo || priceHi * 0.02 || 1;
    const allowance = priceSpan * 1.1;

    const refs = [intrinsicValue, entryLow, entryHigh].filter(
      (v): v is number => typeof v === "number" && isFinite(v) && v > 0,
    );
    const inScale = refs.filter(
      (v) => v >= priceLo - allowance && v <= priceHi + allowance,
    );

    let lo = Math.min(priceLo, ...inScale);
    let hi = Math.max(priceHi, ...inScale);
    const padY = (hi - lo) * 0.08 || hi * 0.04 || 1;
    lo -= padY;
    hi += padY;

    const offScale = {
      intrinsic:
        intrinsicValue != null && intrinsicValue > 0 && !inScale.includes(intrinsicValue)
          ? intrinsicValue > priceHi ? "above" : "below"
          : null,
    };

    const x = (i: number) =>
      PAD.left + scale(i, 0, points.length - 1, 0, PLOT_W);
    const y = (v: number) =>
      PAD.top + scale(v, lo, hi, PRICE_H, 0);

    const volTop = PAD.top + PRICE_H + GAP;
    const volMax = Math.max(...volumes, 1);
    const vy = (v: number) => volTop + VOL_H - (v / volMax) * VOL_H;

    const line = points.map((c, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(2)},${y(c.close).toFixed(2)}`).join(" ");
    const area = `${line} L${x(points.length - 1).toFixed(2)},${(PAD.top + PRICE_H).toFixed(2)} L${x(0).toFixed(2)},${(PAD.top + PRICE_H).toFixed(2)} Z`;

    const first = points[0].close;
    const last = points[points.length - 1].close;

    return {
      points, x, y, vy, lo, hi, volMax, volTop, offScale,
      line, area, first, last,
      changePct: first ? ((last - first) / first) * 100 : 0,
      barW: Math.max(1, (PLOT_W / points.length) * 0.62),
    };
  }, [candles, intrinsicValue, entryLow, entryHigh]);

  function handleMove(evt: React.MouseEvent<SVGSVGElement>) {
    if (!model || !svgRef.current) return;
    const box = svgRef.current.getBoundingClientRect();
    const px = ((evt.clientX - box.left) / box.width) * W;
    const ratio = (px - PAD.left) / PLOT_W;
    const idx = Math.round(ratio * (model.points.length - 1));
    setHover(idx >= 0 && idx < model.points.length ? idx : null);
  }

  const rangeBar = (
    <div className="chart-ranges" role="group" aria-label="Chart time range">
      {RANGES.map((r) => (
        <button
          key={r}
          type="button"
          onClick={() => onRangeChange(r)}
          aria-pressed={r === range}
          className={`chart-range-btn${r === range ? " is-active" : ""}`}
        >
          {r}
        </button>
      ))}
    </div>
  );

  if (loading) {
    return (
      <div className="chart-container">
        <div className="chart-header">
          <h3 className="chart-title">{symbol} — price history</h3>
          {rangeBar}
        </div>
        <div className="chart-empty" role="status">
          <div className="skeleton" style={{ height: 300, width: "100%" }} />
        </div>
      </div>
    );
  }

  if (!model) {
    return (
      <div className="chart-container">
        <div className="chart-header">
          <h3 className="chart-title">{symbol} — price history</h3>
          {rangeBar}
        </div>
        <div className="chart-empty" role="status">
          <BarChart2 className="w-8 h-8" style={{ opacity: 0.3 }} aria-hidden />
          <span>No price history stored for this range</span>
          <span style={{ fontSize: 11 }}>
            Run the ingestion pipeline to populate it
          </span>
        </div>
      </div>
    );
  }

  const { points, x, y, vy, lo, hi, volMax, volTop } = model;
  const up = model.changePct >= 0;
  // Direction is carried by the sign in the label as well as the hue, so the
  // chart is not read by colour alone.
  const lineColor = "var(--brand-primary)";
  const active = hover != null ? points[hover] : null;

  const priceTicks = niceTicks(lo, hi, 5);
  const labelEvery = Math.max(1, Math.floor(points.length / 6));

  return (
    <div className="chart-container">
      <div className="chart-header">
        <div>
          <h3 className="chart-title">{symbol} — price history</h3>
          <div className="chart-subtitle">
            <span className={up ? "price-up" : "price-down"}>
              {up ? "+" : ""}
              {model.changePct.toFixed(2)}% over {range}
            </span>
            {sessionsAvailable != null && (
              <span className="chart-meta">
                {" · "}
                {sessionsAvailable.toLocaleString()} sessions stored
              </span>
            )}
            {resampled && (
              <span className="chart-meta">
                {" · "}aggregated {resampled}
              </span>
            )}
          </div>
        </div>
        {rangeBar}
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="chart-svg"
        preserveAspectRatio="none"
        onMouseMove={handleMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label={`${symbol} price history over ${range}, ${model.changePct.toFixed(1)} percent change`}
      >
        <defs>
          <linearGradient id={`fill-${symbol}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={lineColor} stopOpacity="0.20" />
            <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* Recessive gridlines */}
        {priceTicks.map((t) => (
          <g key={`g${t}`}>
            <line
              x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)}
              stroke="var(--bg-border)" strokeWidth="1" opacity="0.55"
            />
            <text
              x={W - PAD.right + 8} y={y(t) + 3.5}
              className="chart-axis-label" textAnchor="start"
            >
              {currencySymbol}
              {t >= 1000 ? compact(t) : t.toFixed(t < 10 ? 2 : 0)}
            </text>
          </g>
        ))}

        {/* Entry zone: a band, because it is a range and not a line */}
        {entryLow != null && entryHigh != null && entryHigh > entryLow && (
          <g>
            <rect
              x={PAD.left} width={PLOT_W}
              y={y(entryHigh)} height={Math.max(1, y(entryLow) - y(entryHigh))}
              fill="var(--signal-green)" opacity="0.08"
            />
            <text
              x={PAD.left + 6} y={y(entryHigh) - 4}
              className="chart-ref-label" fill="var(--signal-green)"
            >
              Entry zone
            </text>
          </g>
        )}

        {/* Intrinsic value sits outside the plotted range: say so at the edge
            rather than rescaling the whole chart around it. */}
        {model.offScale.intrinsic && (
          <g>
            <line
              x1={PAD.left} x2={W - PAD.right}
              y1={model.offScale.intrinsic === "above" ? PAD.top + 1 : PAD.top + PRICE_H - 1}
              y2={model.offScale.intrinsic === "above" ? PAD.top + 1 : PAD.top + PRICE_H - 1}
              stroke="var(--signal-yellow)" strokeWidth="1.5"
              strokeDasharray="2 6" opacity="0.6"
            />
            <text
              x={PAD.left + 6}
              y={model.offScale.intrinsic === "above" ? PAD.top + 14 : PAD.top + PRICE_H - 6}
              className="chart-ref-label" fill="var(--signal-yellow)"
            >
              {model.offScale.intrinsic === "above" ? "\u25B2" : "\u25BC"} Intrinsic value{" "}
              {currencySymbol}
              {intrinsicValue! >= 1000 ? compact(intrinsicValue!) : intrinsicValue!.toFixed(2)}
              {" — off this scale"}
            </text>
          </g>
        )}

        {/* Intrinsic value: dashed and labelled in text, never colour alone */}
        {intrinsicValue != null && intrinsicValue > 0 && !model.offScale.intrinsic && (
          <g>
            <line
              x1={PAD.left} x2={W - PAD.right}
              y1={y(intrinsicValue)} y2={y(intrinsicValue)}
              stroke="var(--signal-yellow)" strokeWidth="1.5"
              strokeDasharray="5 4" opacity="0.85"
            />
            <text
              x={PAD.left + 6} y={y(intrinsicValue) - 5}
              className="chart-ref-label" fill="var(--signal-yellow)"
            >
              Intrinsic value {currencySymbol}
              {intrinsicValue >= 1000
                ? compact(intrinsicValue)
                : intrinsicValue.toFixed(2)}
            </text>
          </g>
        )}

        <path d={model.area} fill={`url(#fill-${symbol})`} />
        <path
          d={model.line} fill="none" stroke={lineColor}
          strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"
        />

        {/* Volume in its own panel — never a second y-axis on the price plot */}
        <line
          x1={PAD.left} x2={W - PAD.right} y1={volTop + VOL_H} y2={volTop + VOL_H}
          stroke="var(--bg-border)" strokeWidth="1"
        />
        {points.map((c, i) =>
          c.volume ? (
            <rect
              key={`v${i}`}
              x={x(i) - model.barW / 2}
              y={vy(c.volume)}
              width={model.barW}
              height={Math.max(0.5, volTop + VOL_H - vy(c.volume))}
              fill="var(--text-muted)"
              opacity={hover === i ? 0.85 : 0.34}
              rx="1"
            />
          ) : null,
        )}
        <text
          x={W - PAD.right + 8} y={volTop + 10}
          className="chart-axis-label" textAnchor="start"
        >
          {compact(volMax)}
        </text>
        <text
          x={PAD.left + 2} y={volTop - 3}
          className="chart-axis-label" textAnchor="start"
        >
          Volume
        </text>

        {/* Date axis */}
        {points.map((c, i) =>
          i % labelEvery === 0 || i === points.length - 1 ? (
            <text
              key={`d${i}`} x={x(i)} y={H - 8}
              className="chart-axis-label" textAnchor="middle"
            >
              {new Date(c.date).toLocaleDateString("en-GB", {
                day: "2-digit",
                month: "short",
                year: points.length > 300 ? "2-digit" : undefined,
              })}
            </text>
          ) : null,
        )}

        {/* Crosshair */}
        {active && hover != null && (
          <g pointerEvents="none">
            <line
              x1={x(hover)} x2={x(hover)}
              y1={PAD.top} y2={volTop + VOL_H}
              stroke="var(--text-muted)" strokeWidth="1" strokeDasharray="3 3"
            />
            <circle
              cx={x(hover)} cy={y(active.close)} r="4.5"
              fill={lineColor} stroke="var(--bg-surface)" strokeWidth="2"
            />
          </g>
        )}
      </svg>

      {active && (
        <div className="chart-tooltip" role="status" aria-live="polite">
          <span className="chart-tooltip-date">
            {new Date(active.date).toLocaleDateString("en-GB", {
              day: "2-digit", month: "short", year: "numeric",
            })}
          </span>
          <span>
            <b>Close</b> {currencySymbol}
            {active.close.toLocaleString(undefined, {
              minimumFractionDigits: 2, maximumFractionDigits: 2,
            })}
          </span>
          {active.open != null && (
            <span>
              <b>O</b> {active.open.toFixed(2)} <b>H</b> {active.high?.toFixed(2)}{" "}
              <b>L</b> {active.low?.toFixed(2)}
            </span>
          )}
          {active.volume != null && (
            <span>
              <b>Vol</b> {compact(active.volume)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
