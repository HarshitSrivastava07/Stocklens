"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Brain, Send, RefreshCw, Sparkles } from "lucide-react";
import { aiApi } from "@/lib/api";

interface Message {
  role: "user" | "ai";
  content: string;
  symbol?: string;
  disclaimer?: string;
  model?: string;
  ts: Date;
}

export default function AIResearchPage() {
  const router = useRouter();
  const [symbol, setSymbol] = useState("");
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "ai",
      content: "Welcome to AI Research. Enter an NSE symbol above and click Generate to get a research summary powered by Google Gemini.\n\n⚠️ All summaries are based on available model data. This is not investment advice.",
      ts: new Date(),
    },
  ]);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");

  const handleGenerate = async () => {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    setGenerating(true);
    setError("");

    setMessages(m => [...m, { role: "user", content: `Generate research summary for ${sym}`, ts: new Date() }]);

    try {
      // First try to get cached summary
      const cached = await aiApi.getSummary(sym) as any;

      if (cached?.content) {
        setMessages(m => [...m, {
          role: "ai",
          content: cached.content,
          symbol: sym,
          disclaimer: cached.disclaimer,
          model: cached.model_used,
          ts: new Date(),
        }]);
      } else {
        // Generate fresh
        setMessages(m => [...m, { role: "ai", content: `Generating summary for ${sym}...`, ts: new Date() }]);
        const result = await aiApi.generateSummary(sym) as any;
        setMessages(m => [
          ...m.slice(0, -1), // remove the "generating..." message
          {
            role: "ai",
            content: result.content || "Summary generated. Refresh to view.",
            symbol: sym,
            disclaimer: result.disclaimer,
            model: result.model_used,
            ts: new Date(),
          }
        ]);
      }
    } catch (e: any) {
      setError(e.message || "AI generation failed");
      setMessages(m => [...m, {
        role: "ai",
        content: `Error: ${e.message || "Failed to generate summary. Check that GEMINI_API_KEY is set in your .env file."}`,
        ts: new Date(),
      }]);
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col" style={{ background: "var(--bg-base)" }}>
      <div className="topbar">
        <button className="btn btn-ghost" onClick={() => router.push("/")}>
          <ArrowLeft className="w-4 h-4" /> Dashboard
        </button>
        <Brain className="w-5 h-5 text-blue-400" />
        <span className="font-bold" style={{ color: "var(--text-primary)" }}>AI Research</span>
        <span className="text-xs px-2 py-0.5 rounded ml-1" style={{ background: "rgba(33,136,255,0.15)", color: "#2188ff" }}>
          Gemini Flash
        </span>
      </div>

      <div className="flex-1 main-content max-w-3xl mx-auto w-full flex flex-col">
        {/* Messages */}
        <div className="flex-1 space-y-4 mb-6">
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className="max-w-[85%] rounded-lg px-4 py-3"
                style={{
                  background: msg.role === "user"
                    ? "rgba(33,136,255,0.15)"
                    : "var(--bg-surface)",
                  border: `1px solid ${msg.role === "user" ? "rgba(33,136,255,0.3)" : "var(--bg-border)"}`,
                }}
              >
                {msg.role === "ai" && (
                  <div className="flex items-center gap-1 mb-2">
                    <Sparkles className="w-3 h-3 text-blue-400" />
                    <span className="text-xs font-semibold" style={{ color: "#2188ff" }}>
                      Gemini AI {msg.symbol ? `· ${msg.symbol}` : ""}
                    </span>
                  </div>
                )}
                <div
                  className="text-sm whitespace-pre-wrap leading-relaxed"
                  style={{ color: "var(--text-secondary)" }}
                >
                  {msg.content}
                </div>
                {msg.disclaimer && (
                  <div className="mt-3 text-xs p-2 rounded" style={{ background: "rgba(227,179,65,0.08)", color: "var(--text-muted)" }}>
                    {msg.disclaimer}
                  </div>
                )}
                {msg.model && (
                  <div className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
                    Model: {msg.model} · {msg.ts.toLocaleTimeString()}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        {error && <div className="text-sm text-red-400 mb-3">{error}</div>}

        {/* Input bar */}
        <div className="card p-4 sticky bottom-4">
          <div className="flex gap-3">
            <input
              className="input flex-1"
              placeholder="Enter NSE symbol (e.g. RELIANCE, TCS, HDFC)"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase().slice(0, 30))}
              onKeyDown={(e) => e.key === "Enter" && !generating && handleGenerate()}
            />
            <button
              className="btn btn-primary"
              onClick={handleGenerate}
              disabled={generating || !symbol.trim()}
            >
              {generating
                ? <RefreshCw className="w-4 h-4 animate-spin" />
                : <Send className="w-4 h-4" />}
              {generating ? "Generating..." : "Generate"}
            </button>
          </div>
          <div className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
            AI summaries are cached for 4 hours. Generated from valuation data, ML scores, and signals — not from news feeds.
          </div>
        </div>
      </div>
    </div>
  );
}
