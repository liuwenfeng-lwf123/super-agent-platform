"use client";

import { useState, useEffect, useCallback } from "react";
import { fetchCostStats, fetchCostHistory, fetchModelUsage, fetchCompactState } from "@/lib/api";
import { ArrowLeft, DollarSign, Zap, BarChart3, Clock, Cpu, TrendingUp } from "lucide-react";

interface CostSummary {
  session_requests: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  budget_usd?: number;
  budget_remaining_usd?: number;
  is_over_budget?: boolean;
}

interface HistoryEntry {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  model: string;
  thread_id: string;
  mode: string;
  timestamp: string;
  tool_calls: number;
  cache_creation_tokens?: number;
  cache_read_tokens?: number;
}

export default function DashboardPage() {
  const [summary, setSummary] = useState<CostSummary | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [modelUsage, setModelUsage] = useState<Record<string, { input_tokens: number; output_tokens: number; cost_usd: number; requests: number }>>({});
  const [compact, setCompact] = useState<Record<string, unknown> | null>(null);
  const [tab, setTab] = useState<"overview" | "history" | "models">("overview");

  const load = useCallback(async () => {
    try {
      const [s, h, m, c] = await Promise.all([
        fetchCostStats().catch(() => null),
        fetchCostHistory().catch(() => ({ records: [] })),
        fetchModelUsage().catch(() => ({})),
        fetchCompactState().catch(() => null),
      ]);
      if (s) setSummary(s);
      setHistory(Array.isArray(h?.records) ? h.records : Array.isArray(h) ? h : []);
      setModelUsage(m || {});
      if (c) setCompact(c);
    } catch { /* */ }
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);

  const fmt = (n: number) => n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n);
  const fmtCost = (n: number) => n >= 1 ? `$${n.toFixed(2)}` : n >= 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(6)}`;

  const totalCacheWrite = history.reduce((a, h) => a + (h.cache_creation_tokens || 0), 0);
  const totalCacheRead = history.reduce((a, h) => a + (h.cache_read_tokens || 0), 0);

  return (
    <div className="h-screen flex flex-col" style={{ background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-4 border-b" style={{ borderColor: "var(--border-color)" }}>
        <button onClick={() => window.location.href = "/"} className="p-2 rounded-lg hover:bg-white/5">
          <ArrowLeft size={18} />
        </button>
        <BarChart3 size={22} style={{ color: "var(--accent)" }} />
        <h1 className="text-lg font-semibold">成本仪表盘</h1>
        <div className="flex-1" />
        <div className="flex gap-1 rounded-lg p-1" style={{ background: "var(--bg-secondary)" }}>
          {[
            { key: "overview" as const, icon: TrendingUp, label: "概览" },
            { key: "history" as const, icon: Clock, label: "历史" },
            { key: "models" as const, icon: Cpu, label: "模型" },
          ].map(t => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-colors ${tab === t.key ? "text-white" : "text-gray-400 hover:text-gray-200"}`}
              style={tab === t.key ? { background: "var(--accent)" } : {}}>
              <t.icon size={14} />{t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {tab === "overview" && (
          <div className="max-w-5xl mx-auto space-y-6">
            {/* Stat Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                { icon: Zap, label: "请求次数", value: summary?.session_requests ?? 0, color: "text-blue-400" },
                { icon: DollarSign, label: "总花费", value: fmtCost(summary?.total_cost_usd ?? 0), color: "text-green-400" },
                { icon: BarChart3, label: "输入 tokens", value: fmt(summary?.total_input_tokens ?? 0), color: "text-purple-400" },
                { icon: BarChart3, label: "输出 tokens", value: fmt(summary?.total_output_tokens ?? 0), color: "text-orange-400" },
              ].map((c, i) => (
                <div key={i} className="rounded-xl border p-4" style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}>
                  <div className="flex items-center gap-2 mb-2">
                    <c.icon size={16} className={c.color} />
                    <span className="text-xs" style={{ color: "var(--text-secondary)" }}>{c.label}</span>
                  </div>
                  <div className="text-2xl font-bold">{typeof c.value === "number" ? fmt(c.value) : c.value}</div>
                </div>
              ))}
            </div>

            {/* Budget */}
            {summary?.budget_usd != null && summary.budget_usd > 0 && (
              <div className="rounded-xl border p-4" style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium">预算</span>
                  <span className={`text-sm font-medium ${summary.is_over_budget ? "text-red-400" : "text-green-400"}`}>
                    {summary.is_over_budget ? "已超限" : "正常"}
                  </span>
                </div>
                <div className="w-full h-3 rounded-full overflow-hidden" style={{ background: "var(--bg-primary)" }}>
                  <div className={`h-full rounded-full transition-all ${summary.is_over_budget ? "bg-red-500" : "bg-green-500"}`}
                    style={{ width: `${Math.min(100, ((summary.total_cost_usd ?? 0) / summary.budget_usd) * 100)}%` }} />
                </div>
                <div className="flex justify-between mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
                  <span>{fmtCost(summary.total_cost_usd ?? 0)} 已用</span>
                  <span>{fmtCost(summary.budget_usd)} 预算</span>
                </div>
              </div>
            )}

            {/* Cache Stats */}
            {(totalCacheWrite > 0 || totalCacheRead > 0) && (
              <div className="rounded-xl border p-4" style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}>
                <h3 className="text-sm font-medium mb-3">Prompt Cache 统计</h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <div className="text-xs" style={{ color: "var(--text-secondary)" }}>Cache 写入 tokens</div>
                    <div className="text-lg font-bold text-cyan-400">{fmt(totalCacheWrite)}</div>
                  </div>
                  <div>
                    <div className="text-xs" style={{ color: "var(--text-secondary)" }}>Cache 读取 tokens</div>
                    <div className="text-lg font-bold text-emerald-400">{fmt(totalCacheRead)}</div>
                  </div>
                </div>
              </div>
            )}

            {/* Compact State */}
            {compact && (
              <div className="rounded-xl border p-4" style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}>
                <h3 className="text-sm font-medium mb-3">上下文压缩状态</h3>
                <div className="grid grid-cols-3 gap-4 text-sm">
                  <div>
                    <div className="text-xs" style={{ color: "var(--text-secondary)" }}>压缩次数</div>
                    <div className="font-medium">{String(compact.total_compactions ?? 0)}</div>
                  </div>
                  <div>
                    <div className="text-xs" style={{ color: "var(--text-secondary)" }}>边界位置</div>
                    <div className="font-medium">{String(compact.compact_boundary ?? 0)}</div>
                  </div>
                  <div>
                    <div className="text-xs" style={{ color: "var(--text-secondary)" }}>状态</div>
                    <div className={`font-medium ${compact.is_disabled ? "text-red-400" : "text-green-400"}`}>
                      {compact.is_disabled ? "已禁用" : "正常"}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {tab === "history" && (
          <div className="max-w-5xl mx-auto">
            <div className="text-sm mb-3" style={{ color: "var(--text-secondary)" }}>共 {history.length} 条请求记录</div>
            {history.length === 0 ? (
              <div className="text-center py-12" style={{ color: "var(--text-secondary)" }}>暂无请求记录</div>
            ) : (
              <div className="space-y-2">
                {[...history].reverse().map((h, i) => (
                  <div key={i} className="rounded-lg border p-3 flex items-center gap-4" style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 text-sm">
                        <span className="font-mono text-xs px-2 py-0.5 rounded bg-blue-500/20 text-blue-300">{h.model || "unknown"}</span>
                        <span className="text-xs" style={{ color: "var(--text-secondary)" }}>{h.mode}</span>
                        {h.cache_read_tokens ? <span className="text-xs text-emerald-400">cache hit</span> : null}
                      </div>
                      <div className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>
                        {h.timestamp?.slice(0, 19)} · {h.thread_id?.slice(0, 8)}
                      </div>
                    </div>
                    <div className="text-right text-sm space-y-0.5">
                      <div className="font-medium">{fmtCost(h.cost_usd)}</div>
                      <div className="text-xs" style={{ color: "var(--text-secondary)" }}>
                        {fmt(h.input_tokens)} in · {fmt(h.output_tokens)} out
                        {h.tool_calls ? ` · ${h.tool_calls} tools` : ""}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {tab === "models" && (
          <div className="max-w-5xl mx-auto">
            <h2 className="text-base font-medium mb-4">按模型统计</h2>
            {Object.keys(modelUsage).length === 0 ? (
              <div className="text-center py-12" style={{ color: "var(--text-secondary)" }}>暂无数据</div>
            ) : (
              <div className="space-y-3">
                {Object.entries(modelUsage).sort((a, b) => (b[1].cost_usd ?? 0) - (a[1].cost_usd ?? 0)).map(([model, u]) => (
                  <div key={model} className="rounded-lg border p-4" style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}>
                    <div className="flex items-center justify-between mb-2">
                      <span className="font-mono text-sm">{model}</span>
                      <span className="text-sm font-bold">{fmtCost(u.cost_usd)}</span>
                    </div>
                    <div className="flex gap-6 text-xs" style={{ color: "var(--text-secondary)" }}>
                      <span>{u.requests} 次请求</span>
                      <span>{fmt(u.input_tokens)} 输入</span>
                      <span>{fmt(u.output_tokens)} 输出</span>
                    </div>
                    {/* Usage bar */}
                    <div className="mt-2 flex gap-1 h-2 rounded-full overflow-hidden" style={{ background: "var(--bg-primary)" }}>
                      <div className="bg-blue-500 rounded-full" style={{ width: `${Math.max(2, u.input_tokens / (u.input_tokens + u.output_tokens + 1) * 100)}%` }} />
                      <div className="bg-orange-500 rounded-full" style={{ width: `${Math.max(2, u.output_tokens / (u.input_tokens + u.output_tokens + 1) * 100)}%` }} />
                    </div>
                    <div className="flex gap-4 mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
                      <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-blue-500 inline-block" /> 输入</span>
                      <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-orange-500 inline-block" /> 输出</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
