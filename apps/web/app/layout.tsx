import type { Metadata } from "next";
import "@/app/globals.css";
import Sidebar from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "StockLens — NSE Stock Intelligence Platform",
  description:
    "Personal NSE stock research platform with real-time prices, intrinsic value calculations, ML-based scoring, and AI-powered analysis.",
  keywords: ["NSE", "stock analysis", "intrinsic value", "DCF", "stock screener"],
  robots: "noindex, nofollow",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <div className="app-shell">
          <Sidebar />
          <main className="app-content">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
