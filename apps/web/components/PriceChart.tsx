"use client";
import { useEffect, useRef, useState } from "react";
import { BarChart2 } from "lucide-react";

interface Candle {
  time: number; // unix seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

interface PriceChartProps {
  symbol: string;
  candles: Candle[];
  height?: number;
}

const W = 900; // virtual canvas width
const PADDING = { top: 20, right: 60, bottom: 40, left: 12 };
const BAR_GAP = 0.25;

function lerp(v: number, inMin: number, inMax: number, outMin: number, outMax: number) {
  if (inMax === inMin) return (outMin + outMax) / 2;
  return outMin + ((v - inMin) / (inMax - inMin)) * (outMax - outMin);
}

function formatPrice(v: number) {
  return v >= 1000
    ? `₹${(v / 1000).toFixed(1)}k`
    : `₹${v.toFixed(v < 10 ? 2 : 0)}`;
}

function formatDate(ts: number) {
  const d = new Date(ts * 1000);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

export default function PriceChart({ symbol, candles, height = 380 }: PriceChartProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; candle: Candle } | null>(null);
  const [interval, setInterval_] = useState<"1D" | "1W" | "1M">("1D");

  if (!candles || candles.length === 0) {
    return (
      <div className="chart-container">
        <div className="chart-empty">
          <BarChart2 className="w-8 h-8 opacity-30" />
          <span>No price history yet</span>
          <span style={{ fontSize: 11 }}>EOD data builds after first trading day close</span>
        </div>
      </div>
    );
  }

  // Resample if needed
  let data = [...candles].sort((a, b) => a.time - b.time);
  if (interval === "1W") {
    // Group into weeks
    const weeks: Record<string, Candle[]> = {};
    data.forEach((c) => {
      const d = new Date(c.time * 1000);
      d.setDate(d.getDate() - d.getDay());
      const key = d.toISOString().slice(0, 10);
      if (!weeks[key]) weeks[key] = [];
      weeks[key].push(c);
    });
    data = Object.entries(weeks).map(([, cs]) => ({
      time: cs[0].time,
      open: cs[0].open,
      high: Math.max(...cs.map((c) => c.high)),
      low: Math.min(...cs.map((c) => c.low)),
      close: cs[cs.length - 1].close,
      volume: cs.reduce((s, c) => s + (c.volume || 0), 0),
    }));
  }

  const H = height;
  const chartH = H - PADDING.top - PADDING.bottom;
  const chartW = W - PADDING.left - PADDING.right;

  const prices = data.flatMap((c) => [c.high, c.low]);
  const priceMin = Math.min(...prices) * 0.995;
  const priceMax = Math.max(...prices) * 1.005;

  const barW = Math.max(2, (chartW / data.length) * (1 - BAR_GAP));
  const barStep = chartW / data.length;

  const toX = (i: number) => PADDING.left + i * barStep + barStep / 2;
  const toY = (p: number) => PADDING.top + lerp(p, priceMin, priceMax, chartH, 0);

  // Y-axis ticks
  const ticks = 5;
  const yTicks = Array.from({ length: ticks + 1 }, (_, i) =>
    priceMin + ((priceMax - priceMin) * i) / ticks
  );

  // X-axis: show every Nth label
  const xStep = Math.max(1, Math.floor(data.length / 8));

  return (
    <div className="chart-container" style={{ height }}>
      {/* Interval selector */}
      <div style={{ position: "absolute", top: 10, right: 12, display: "flex", gap: 4, zIndex: 10 }}>
        {(["1D", "1W", "1M"] as const).map((iv) => (
          <button
            key={iv}
            onClick={() => setInterval_(iv)}
            style={{
              padding: "3px 8px",
              fontSize: 11,
              borderRadius: 4,
              border: "1px solid var(--bg-border)",
              background: interval === iv ? "var(--brand-primary)" : "var(--bg-elevated)",
              color: interval === iv ? "white" : "var(--text-muted)",
              cursor: "pointer",
            }}
          >
            {iv}
          </button>
        ))}
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ width: "100%", height: "100%", display: "block" }}
        onMouseLeave={() => setTooltip(null)}
      >
        {/* Grid lines */}
        {yTicks.map((t, i) => (
          <g key={i}>
            <line
              x1={PADDING.left} y1={toY(t)}
              x2={W - PADDING.right} y2={toY(t)}
              stroke="rgba(255,255,255,0.05)" strokeWidth={1}
            />
            <text
              x={W - PADDING.right + 6} y={toY(t) + 4}
              fill="#6e7681" fontSize={9} textAnchor="start"
            >
              {formatPrice(t)}
            </text>
          </g>
        ))}

        {/* X-axis labels */}
        {data.map((c, i) => i % xStep === 0 && (
          <text key={i} x={toX(i)} y={H - 6} fill="#6e7681" fontSize={9} textAnchor="middle">
            {formatDate(c.time)}
          </text>
        ))}

        {/* Candles */}
        {data.map((c, i) => {
          const x = toX(i);
          const isGreen = c.close >= c.open;
          const color = isGreen ? "#3fb950" : "#f85149";
          const bodyTop = toY(Math.max(c.open, c.close));
          const bodyH = Math.max(1, Math.abs(toY(c.open) - toY(c.close)));

          return (
            <g
              key={i}
              onMouseMove={(e) => {
                const svg = svgRef.current;
                if (!svg) return;
                const rect = svg.getBoundingClientRect();
                setTooltip({
                  x: ((e.clientX - rect.left) / rect.width) * W,
                  y: ((e.clientY - rect.top) / rect.height) * H,
                  candle: c,
                });
              }}
              style={{ cursor: "crosshair" }}
            >
              {/* Wick */}
              <line
                x1={x} y1={toY(c.high)}
                x2={x} y2={toY(c.low)}
                stroke={color} strokeWidth={1.5}
              />
              {/* Body */}
              <rect
                x={x - barW / 2} y={bodyTop}
                width={barW} height={bodyH}
                fill={color} rx={1}
              />
            </g>
          );
        })}

        {/* Tooltip */}
        {tooltip && (() => {
          const { x, y, candle } = tooltip;
          const isGreen = candle.close >= candle.open;
          const tw = 140, th = 90;
          const tx = x + tw > W - PADDING.right ? x - tw - 8 : x + 8;
          const ty = y + th > H ? H - th - 4 : y;

          return (
            <g>
              <line x1={x} y1={PADDING.top} x2={x} y2={H - PADDING.bottom}
                stroke="rgba(255,255,255,0.15)" strokeWidth={1} strokeDasharray="4,3" />
              <rect x={tx} y={ty} width={tw} height={th} rx={4}
                fill="var(--bg-elevated)" stroke="var(--bg-border)" strokeWidth={1} />
              <text x={tx + 8} y={ty + 16} fill="#8b949e" fontSize={9}>{formatDate(candle.time)}</text>
              <text x={tx + 8} y={ty + 32} fill={isGreen ? "#3fb950" : "#f85149"} fontSize={10} fontWeight="600">
                C: {formatPrice(candle.close)}
              </text>
              <text x={tx + 8} y={ty + 46} fill="#6e7681" fontSize={9}>O: {formatPrice(candle.open)}</text>
              <text x={tx + 8} y={ty + 58} fill="#3fb950" fontSize={9}>H: {formatPrice(candle.high)}</text>
              <text x={tx + 8} y={ty + 70} fill="#f85149" fontSize={9}>L: {formatPrice(candle.low)}</text>
              {candle.volume && (
                <text x={tx + 8} y={ty + 82} fill="#6e7681" fontSize={9}>
                  V: {(candle.volume / 1e5).toFixed(1)}L
                </text>
              )}
            </g>
          );
        })()}
      </svg>
    </div>
  );
}
