"use client";
import { useRouter } from "next/navigation";
import { AlertTriangle, Info } from "lucide-react";

interface ErrorBoundaryState {
  hasError: boolean;
  message: string;
}

import React from "react";

export default class ErrorBoundary extends React.Component<
  { children: React.ReactNode; fallback?: string },
  ErrorBoundaryState
> {
  constructor(props: any) {
    super(props);
    this.state = { hasError: false, message: "" };
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, message: error.message };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Log to console only (no external reporting — personal app)
    console.error("[StockLens Error]", error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          className="flex flex-col items-center justify-center p-12 text-center"
          style={{ minHeight: 320, color: "var(--text-muted)" }}
        >
          <AlertTriangle className="w-10 h-10 mb-3 text-yellow-400 opacity-70" />
          <div className="text-sm font-semibold mb-1" style={{ color: "var(--text-primary)" }}>
            {this.props.fallback || "Something went wrong"}
          </div>
          <div className="text-xs max-w-sm" style={{ color: "var(--text-muted)" }}>
            {this.state.message || "An unexpected error occurred in this component."}
          </div>
          <button
            className="btn btn-ghost mt-4 text-xs"
            onClick={() => this.setState({ hasError: false, message: "" })}
          >
            Try Again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
