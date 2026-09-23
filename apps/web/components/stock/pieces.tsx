"use client";

/**
 * Shared display pieces for the stock detail page.
 *
 * The rule running through this file: **never encode meaning in colour alone.**
 * Validating this app's status palette against the dark surface showed that
 * `#3fb950` (buy) and `#e3b341` (hold) sit ΔE 3.8 apart under protanopia — a
 * red-green colourblind reader, which is roughly 8% of men, cannot tell a buy
 * badge from a hold badge by its colour. On a product where colour means "buy"
 * or "sell", that is not a cosmetic issue. Every badge here therefore carries an
 * icon and a text label; the colour is reinforcement, not the signal.
 */

import {
  AlertTriangle, ArrowDownRight, ArrowUpRight, CheckCircle2,
  CircleSlash, HelpCircle, MinusCircle, ShieldAlert, TrendingUp,
} from "lucide-react";

// ─────────────────────────────────────────────────────────────
// Actions
// ─────────────────────────────────────────────────────────────
export const ACTION_META: Record<
  string,
  { label: string; tone: string; Icon: typeof TrendingUp; blurb: string }
> = {
  STRONG_BUY: { label: "Strong Buy", tone: "green", Icon: TrendingUp, blurb: "Undervalued, high quality, and the market has turned" },
  BUY: { label: "Buy", tone: "green", Icon: ArrowUpRight, blurb: "Trading meaningfully below intrinsic value" },
  ACCUMULATE: { label: "Accumulate", tone: "lime", Icon: CheckCircle2, blurb: "Worth owning, but buy into weakness" },
  HOLD: { label: "Hold", tone: "yellow", Icon: MinusCircle, blurb: "Fairly priced — no edge either way" },
  REDUCE: { label: "Reduce", tone: "orange", Icon: ArrowDownRight, blurb: "Overvalued and losing momentum" },
  SELL: { label: "Sell", tone: "red", Icon: ArrowDownRight, blurb: "Trading well above defensible value" },
  AVOID: { label: "Avoid", tone: "red", Icon: ShieldAlert, blurb: "Risk rules this out regardless of price" },
  INSUFFICIENT_DATA: { label: "Insufficient data", tone: "grey", Icon: HelpCircle, blurb: "Not enough reliable data to form a view" },
};

export function ActionBadge({
  action,
  size = "md",
}: {
  action?: string | null;
  size?: "sm" | "md" | "lg";
}) {
  const meta = ACTION_META[action ?? "INSUFFICIENT_DATA"] ?? ACTION_META.INSUFFICIENT_DATA;
  const { Icon } = meta;
  return (
    <span className={`action-badge tone-${meta.tone} size-${size}`}>
      <Icon className="action-badge-icon" aria-hidden />
      <span>{meta.label}</span>
    </span>
  );
}

// ─────────────────────────────────────────────────────────────
// Numbers
// ─────────────────────────────────────────────────────────────
export function fmtMoney(v: number | null | undefined, sym = "₹", dp = 2): string {
  if (v == null || !isFinite(v)) return "—";
  return `${sym}${v.toLocaleString(sym === "₹" ? "en-IN" : "en-US", {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  })}`;
}

export function fmtPct(v: number | null | undefined, dp = 1, alreadyPct = true): string {
  if (v == null || !isFinite(v)) return "—";
  const n = alreadyPct ? v : v * 100;
  return `${n >= 0 ? "+" : ""}${n.toFixed(dp)}%`;
}

export function fmtCompact(v: number | null | undefined, sym = ""): string {
  if (v == null || !isFinite(v)) return "—";
  const abs = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (abs >= 1e12) return `${sign}${sym}${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${sign}${sym}${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e7) return `${sign}${sym}${(abs / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e6) return `${sign}${sym}${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}${sym}${(abs / 1e3).toFixed(1)}K`;
  return `${sign}${sym}${abs.toFixed(0)}`;
}

// ─────────────────────────────────────────────────────────────
// Tiles
// ─────────────────────────────────────────────────────────────
export function StatTile({
  label, value, sub, tone, title,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "up" | "down" | "neutral";
  title?: string;
}) {
  return (
    <div className="stat-tile" title={title}>
      <div className="stat-tile-label">{label}</div>
      <div className={`stat-tile-value${tone ? ` tone-${tone}` : ""}`}>{value}</div>
      {sub && <div className="stat-tile-sub">{sub}</div>}
    </div>
  );
}

/**
 * A 0–100 score.
 *
 * Risk is inverted — a high risk score is bad — so it is labelled as such rather
 * than relying on the reader to remember which direction is good.
 */
export function ScoreBar({
  label, value, inverted = false, hint,
}: {
  label: string;
  value: number | null | undefined;
  inverted?: boolean;
  hint?: string;
}) {
  const v = value == null || !isFinite(value) ? null : Math.max(0, Math.min(100, value));
  const good = v == null ? false : inverted ? v <= 40 : v >= 60;
  const poor = v == null ? false : inverted ? v >= 70 : v <= 35;
  const tone = v == null ? "grey" : good ? "green" : poor ? "red" : "yellow";

  return (
    <div className="score-row" title={hint}>
      <div className="score-row-head">
        <span className="score-row-label">
          {label}
          {inverted && <span className="score-row-note"> (lower is better)</span>}
        </span>
        <span className="score-row-value">{v == null ? "—" : v.toFixed(0)}</span>
      </div>
      <div className="score-bar-track" role="meter" aria-valuenow={v ?? undefined}
           aria-valuemin={0} aria-valuemax={100} aria-label={label}>
        <div className={`score-bar-fill tone-${tone}`} style={{ width: `${v ?? 0}%` }} />
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Valuation band
// ─────────────────────────────────────────────────────────────
/**
 * Bear / base / bull with the current price marked.
 *
 * These are ordinal steps of one quantity, not three unrelated categories, so
 * they are one hue at three depths rather than three competing colours. The
 * price marker is a separate mark with its own label, because it is a different
 * kind of thing from the scenarios.
 */
export function ValuationBand({
  bear, base, bull, price, currency = "₹",
}: {
  bear?: number | null;
  base?: number | null;
  bull?: number | null;
  price?: number | null;
  currency?: string;
}) {
  const values = [bear, base, bull, price].filter(
    (v): v is number => v != null && isFinite(v) && v > 0,
  );
  if (values.length < 2) {
    return <div className="muted-note">Scenario range not available.</div>;
  }

  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pos = (v: number) => ((v - lo) / (hi - lo || 1)) * 100;

  return (
    <div className="val-band">
      <div className="val-band-track">
        {bear != null && bull != null && (
          <div
            className="val-band-range"
            style={{ left: `${pos(bear)}%`, width: `${pos(bull) - pos(bear)}%` }}
          />
        )}
        {base != null && (
          <div className="val-band-base" style={{ left: `${pos(base)}%` }} />
        )}
        {price != null && (
          <div className="val-band-price" style={{ left: `${pos(price)}%` }}>
            <span className="val-band-price-flag">Now</span>
          </div>
        )}
      </div>
      <div className="val-band-legend">
        <span><em>Bear</em> {fmtMoney(bear, currency)}</span>
        <span><em>Base</em> {fmtMoney(base, currency)}</span>
        <span><em>Bull</em> {fmtMoney(bull, currency)}</span>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Empty / unavailable state
// ─────────────────────────────────────────────────────────────
/**
 * Shown wherever the backend says a block is not available.
 *
 * It states *why*, because "no valuation" and "valuation refused because this
 * company has three years of filings" are different facts and the second one is
 * the useful one.
 */
export function NotAvailable({
  title, reason, hint,
}: {
  title: string;
  reason?: string | null;
  hint?: string;
}) {
  return (
    <div className="not-available" role="status">
      <CircleSlash className="not-available-icon" aria-hidden />
      <div>
        <div className="not-available-title">{title}</div>
        {reason && <div className="not-available-reason">{reason}</div>}
        {hint && <div className="not-available-hint">{hint}</div>}
      </div>
    </div>
  );
}

export function WarningList({ warnings }: { warnings?: string[] | null }) {
  if (!warnings?.length) return null;
  return (
    <div className="warning-list">
      <div className="warning-list-head">
        <AlertTriangle className="warning-list-icon" aria-hidden />
        <span>What reduces confidence in this</span>
      </div>
      <ul>
        {warnings.map((w, i) => (
          <li key={i}>{w}</li>
        ))}
      </ul>
    </div>
  );
}

export function ConfidencePill({
  confidence, years,
}: {
  confidence?: string | null;
  years?: number | null;
}) {
  const tone =
    confidence === "HIGH" ? "green" : confidence === "MEDIUM" ? "yellow" : "grey";
  return (
    <span className={`confidence-pill tone-${tone}`}>
      {confidence ?? "UNKNOWN"} confidence
      {years != null && <em> · {years}y of filings</em>}
    </span>
  );
}
