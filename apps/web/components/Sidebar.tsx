"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard, Star, Bell, TrendingUp,
  Filter, Brain, Zap, ChevronLeft,
  ChevronRight, Layers, BarChart2, Shield, Cog,
} from "lucide-react";
import { useState, type ElementType } from "react";

// BUG FIX: Was using Settings icon for BOTH /admin and /settings — now distinct icons
// Admin → Shield, Settings → Cog
const NAV_ITEMS = [
  { href: "/",              icon: LayoutDashboard, label: "Dashboard"   },
  { href: "/screener",      icon: Filter,          label: "Screener"    },
  { href: "/sectors",       icon: Layers,          label: "Sectors"     },
  { href: "/fo",            icon: BarChart2,        label: "F&O"         },
  { href: "/watchlist",     icon: Star,            label: "Watchlist"   },
  { href: "/portfolio",     icon: TrendingUp,      label: "Portfolio"   },
  { href: "/alerts",        icon: Bell,            label: "Alerts"      },
  { href: "/ai",            icon: Brain,           label: "AI Research" },
  { href: "/backtest",      icon: Zap,             label: "Backtest"    },
  { href: "/admin",         icon: Shield,          label: "Admin"       }, // BUG FIX: was Settings (same as /settings)
  { href: "/settings",      icon: Cog,             label: "Settings"    }, // BUG FIX: was Settings (duplicate)
] as const;

// ─── Types ────────────────────────────────────────────────────
type NavItem = {
  href: string;
  icon: ElementType;
  label: string;
};

export default function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside
      className="sidebar"
      style={{
        width: collapsed ? 64 : 220,
        transition: "width 0.25s cubic-bezier(0.4,0,0.2,1)",
        flexShrink: 0,
        height: "100vh",
        position: "sticky",
        top: 0,
        display: "flex",
        flexDirection: "column",
        background: "var(--bg-surface)",
        borderRight: "1px solid var(--bg-border)",
        zIndex: 100,
        overflow: "hidden",
      }}
    >
      {/* Logo */}
      <div
        className="flex items-center gap-3 px-4 py-5"
        style={{ borderBottom: "1px solid var(--bg-border)", minHeight: 64 }}
      >
        <div
          className="w-8 h-8 rounded-lg flex items-center justify-center font-bold text-sm shrink-0"
          style={{ background: "var(--brand)", color: "white" }}
        >
          SL
        </div>
        {!collapsed && (
          <div>
            <div className="font-bold text-sm leading-tight" style={{ color: "var(--text-primary)" }}>
              StockLens
            </div>
            <div className="text-xs" style={{ color: "var(--text-muted)" }}>NSE Intelligence</div>
          </div>
        )}
      </div>

      {/* Nav links */}
      <nav className="flex-1 px-2 py-3 overflow-y-auto">
        {(NAV_ITEMS as unknown as NavItem[]).map(({ href, icon: Icon, label }) => {
          // BUG FIX: exact match for "/" prevents every route from being "active"
          const isActive =
            href === "/" ? pathname === "/" : pathname.startsWith(href);

          return (
            <Link
              key={href}
              href={href}
              // BUG FIX: title only shown when collapsed (tooltip on hover for icon-only mode)
              title={collapsed ? label : undefined}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "9px 12px",
                borderRadius: 8,
                marginBottom: 2,
                textDecoration: "none",
                background: isActive ? "rgba(33,136,255,0.12)" : "transparent",
                color: isActive ? "#2188ff" : "var(--text-muted)",
                fontWeight: isActive ? 600 : 400,
                fontSize: 13,
                transition: "all 0.15s",
                whiteSpace: "nowrap",
                overflow: "hidden",
              }}
              onMouseEnter={(e) => {
                if (!isActive) {
                  (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)";
                  (e.currentTarget as HTMLElement).style.color = "var(--text-primary)";
                }
              }}
              onMouseLeave={(e) => {
                if (!isActive) {
                  (e.currentTarget as HTMLElement).style.background = "transparent";
                  (e.currentTarget as HTMLElement).style.color = "var(--text-muted)";
                }
              }}
            >
              <Icon className="w-4 h-4 shrink-0" />
              {!collapsed && <span>{label}</span>}
              {!collapsed && isActive && (
                <span
                  className="ml-auto w-1.5 h-1.5 rounded-full"
                  style={{ background: "#2188ff" }}
                />
              )}
            </Link>
          );
        })}
      </nav>

      {/* Collapse toggle + version */}
      <div
        style={{ borderTop: "1px solid var(--bg-border)", padding: "12px 8px" }}
      >
        {!collapsed && (
          <div className="text-xs px-3 pb-2" style={{ color: "var(--text-muted)" }}>
            v1.0 · Personal use only
          </div>
        )}
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="w-full flex items-center justify-center p-2 rounded-lg"
          style={{
            background: "transparent",
            color: "var(--text-muted)",
            cursor: "pointer",
            border: "none",
            transition: "background 0.15s",
          }}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed
            ? <ChevronRight className="w-4 h-4" />
            : <ChevronLeft className="w-4 h-4" />}
        </button>
      </div>
    </aside>
  );
}
