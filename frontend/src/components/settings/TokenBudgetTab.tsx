"use client";

import { useState, useEffect } from "react";
import {
  fetchTokenBudget,
  toggleTokenFeature,
  fetchTokenPresets,
  applyTokenPreset,
  fetchDailyUsage,
  setDailyBudget,
  fetchTokenHistory,
  fetchCacheStats,
  getExportCsvUrl,
} from "@/lib/api";
import type { TokenBudgetResponse, PresetInfo, DailyUsage, DayUsage, CacheStats } from "@/lib/api";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

export default function TokenBudgetTab() {
  const [tokenBudget, setTokenBudget] = useState<TokenBudgetResponse | null>(null);
  const [tokenTogglingId, setTokenTogglingId] = useState<string | null>(null);
  const [tokenMsg, setTokenMsg] = useState<string | null>(null);
  const [presets, setPresets] = useState<Record<string, PresetInfo>>({});
  const [activePreset, setActivePreset] = useState<string | null>(null);
  const [dailyUsage, setDailyUsage] = useState<DailyUsage | null>(null);
  const [budgetInput, setBudgetInput] = useState("");
  const [presetApplying, setPresetApplying] = useState<string | null>(null);
  const [historyDays, setHistoryDays] = useState<DayUsage[]>([]);
  const [historyMetric, setHistoryMetric] = useState<"total_tokens" | "cost_usd">("total_tokens");
  const [cacheStats, setCacheStats] = useState<CacheStats | null>(null);

  useEffect(() => { loadAll(); }, []);

  const loadAll = async () => {
    try {
      const [data, presetsData, daily, history, cache] = await Promise.all([
        fetchTokenBudget(),
        fetchTokenPresets(),
        fetchDailyUsage(),
        fetchTokenHistory(7),
        fetchCacheStats(),
      ]);
      setTokenBudget(data);
      setPresets(presetsData.presets || {});
      setActivePreset(presetsData.active_preset);
      setDailyUsage(daily);
      setHistoryDays(history.days || []);
      setCacheStats(cache);
      if (daily.budget > 0) setBudgetInput(String(daily.budget));
    } catch {
      setTokenBudget(null);
    }
  };

  return (
    <div className="max-w-2xl space-y-5">
      <h2 className="text-lg font-semibold">Token 预算控制</h2>
      <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
        管理 Token 消耗：设置每日上限、一键切换模式、逐个开关功能。
      </p>

      {/* Daily Usage & Budget */}
      {dailyUsage && (
        <div className="p-4 rounded-xl border space-y-3" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">今日用量 ({dailyUsage.date})</h3>
            <span className="text-xs" style={{ color: "var(--text-secondary)" }}>{dailyUsage.requests} 次请求</span>
          </div>
          <div className="grid grid-cols-4 gap-2 text-center">
            <div>
              <div className="text-base font-bold text-blue-400">{dailyUsage.input_tokens > 1000 ? `${(dailyUsage.input_tokens / 1000).toFixed(1)}K` : dailyUsage.input_tokens}</div>
              <div className="text-[10px]" style={{ color: "var(--text-secondary)" }}>输入</div>
            </div>
            <div>
              <div className="text-base font-bold text-green-400">{dailyUsage.output_tokens > 1000 ? `${(dailyUsage.output_tokens / 1000).toFixed(1)}K` : dailyUsage.output_tokens}</div>
              <div className="text-[10px]" style={{ color: "var(--text-secondary)" }}>输出</div>
            </div>
            <div>
              <div className="text-base font-bold text-purple-400">{dailyUsage.total_tokens > 1000 ? `${(dailyUsage.total_tokens / 1000).toFixed(1)}K` : dailyUsage.total_tokens}</div>
              <div className="text-[10px]" style={{ color: "var(--text-secondary)" }}>合计</div>
            </div>
            <div>
              <div className="text-base font-bold text-yellow-400">${dailyUsage.cost_usd.toFixed(4)}</div>
              <div className="text-[10px]" style={{ color: "var(--text-secondary)" }}>费用</div>
            </div>
          </div>
          {dailyUsage.budget > 0 && (
            <div>
              <div className="flex justify-between text-[10px] mb-1" style={{ color: "var(--text-secondary)" }}>
                <span>已用 {dailyUsage.budget_used_pct}%</span>
                <span>剩余 {dailyUsage.remaining > 1000 ? `${(dailyUsage.remaining / 1000).toFixed(0)}K` : dailyUsage.remaining}</span>
              </div>
              <div className="h-2 rounded-full overflow-hidden" style={{ background: "rgba(255,255,255,0.1)" }}>
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${Math.min(100, dailyUsage.budget_used_pct)}%`,
                    background: dailyUsage.budget_used_pct > 90 ? "#ef4444" : dailyUsage.budget_used_pct > 70 ? "#f59e0b" : "#22c55e",
                  }}
                />
              </div>
              {dailyUsage.is_over_budget && (
                <div className="mt-2 text-xs px-3 py-1.5 rounded-lg bg-red-900/30 border border-red-800 text-red-300">
                  已超出今日预算！新的 LLM 调用将被拦截。
                </div>
              )}
            </div>
          )}
          <div className="flex items-center gap-2">
            <input
              value={budgetInput}
              onChange={(e) => setBudgetInput(e.target.value)}
              placeholder="每日上限（如 500000），0=不限"
              className="flex-1 px-3 py-1.5 rounded-lg border text-sm outline-none"
              style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
            />
            <button
              onClick={async () => {
                const val = parseInt(budgetInput) || 0;
                const res = await setDailyBudget(val);
                setTokenMsg(res.message);
                await loadAll();
              }}
              className="px-3 py-1.5 rounded-lg text-xs font-medium"
              style={{ background: "var(--accent)", color: "#fff" }}
            >
              设置上限
            </button>
            {dailyUsage.budget > 0 && (
              <button
                onClick={async () => {
                  setBudgetInput("");
                  const res = await setDailyBudget(0);
                  setTokenMsg(res.message);
                  await loadAll();
                }}
                className="px-3 py-1.5 rounded-lg text-xs border"
                style={{ borderColor: "var(--border-color)", color: "var(--text-secondary)" }}
              >
                取消限制
              </button>
            )}
          </div>
        </div>
      )}

      {/* 7-Day Trend Chart */}
      {historyDays.length > 0 && (
        <div className="p-4 rounded-xl border space-y-3" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">7 天趋势</h3>
            <div className="flex gap-1">
              <button
                onClick={() => setHistoryMetric("total_tokens")}
                className="px-2 py-0.5 rounded text-[10px]"
                style={{
                  background: historyMetric === "total_tokens" ? "var(--accent)" : "transparent",
                  color: historyMetric === "total_tokens" ? "#fff" : "var(--text-secondary)",
                  border: historyMetric === "total_tokens" ? "none" : "1px solid var(--border-color)",
                }}
              >
                Tokens
              </button>
              <button
                onClick={() => setHistoryMetric("cost_usd")}
                className="px-2 py-0.5 rounded text-[10px]"
                style={{
                  background: historyMetric === "cost_usd" ? "var(--accent)" : "transparent",
                  color: historyMetric === "cost_usd" ? "#fff" : "var(--text-secondary)",
                  border: historyMetric === "cost_usd" ? "none" : "1px solid var(--border-color)",
                }}
              >
                费用
              </button>
            </div>
          </div>
          <div style={{ width: "100%", height: 180 }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={historyDays} margin={{ top: 5, right: 5, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorMetric" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fill: "rgba(255,255,255,0.4)" }}
                  tickFormatter={(v: string) => v.slice(5)}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 10, fill: "rgba(255,255,255,0.4)" }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v: number) =>
                    historyMetric === "cost_usd" ? `$${v.toFixed(2)}` : v > 1000000 ? `${(v / 1000000).toFixed(1)}M` : v > 1000 ? `${(v / 1000).toFixed(0)}K` : String(v)
                  }
                  width={55}
                />
                <Tooltip
                  contentStyle={{ background: "#1e1e2e", border: "1px solid #333", borderRadius: 8, fontSize: 12 }}
                  labelStyle={{ color: "#888" }}
                  formatter={(value: unknown) => {
                    const v = Number(value) || 0;
                    return historyMetric === "cost_usd"
                      ? [`$${v.toFixed(4)}`, "费用"]
                      : [`${v.toLocaleString()}`, "Tokens"];
                  }}
                  labelFormatter={(label: unknown) => String(label)}
                />
                <Area
                  type="monotone"
                  dataKey={historyMetric}
                  stroke="#8b5cf6"
                  strokeWidth={2}
                  fillOpacity={1}
                  fill="url(#colorMetric)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Prompt Cache Stats */}
      {cacheStats && cacheStats.total_records > 0 && (
        <div className="p-4 rounded-xl border space-y-3" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
          <h3 className="text-sm font-medium">Prompt Cache 统计</h3>
          <div className="grid grid-cols-4 gap-2 text-center">
            <div>
              <div className="text-base font-bold text-cyan-400">{cacheStats.cache_hit_rate}%</div>
              <div className="text-[10px]" style={{ color: "var(--text-secondary)" }}>Cache 命中率</div>
            </div>
            <div>
              <div className="text-base font-bold text-green-400">
                {cacheStats.cache_read_tokens > 1000 ? `${(cacheStats.cache_read_tokens / 1000).toFixed(1)}K` : cacheStats.cache_read_tokens}
              </div>
              <div className="text-[10px]" style={{ color: "var(--text-secondary)" }}>Cache 读取</div>
            </div>
            <div>
              <div className="text-base font-bold text-amber-400">
                {cacheStats.cache_creation_tokens > 1000 ? `${(cacheStats.cache_creation_tokens / 1000).toFixed(1)}K` : cacheStats.cache_creation_tokens}
              </div>
              <div className="text-[10px]" style={{ color: "var(--text-secondary)" }}>Cache 写入</div>
            </div>
            <div>
              <div className="text-base font-bold text-emerald-400">${cacheStats.saved_usd.toFixed(4)}</div>
              <div className="text-[10px]" style={{ color: "var(--text-secondary)" }}>Cache 节省</div>
            </div>
          </div>
          <div className="flex items-center gap-2 text-[10px]" style={{ color: "var(--text-secondary)" }}>
            <span>{cacheStats.records_with_cache} / {cacheStats.total_records} 次请求使用了 Cache</span>
            <span>·</span>
            <span>总费用 ${cacheStats.total_cost_usd.toFixed(2)}</span>
          </div>
        </div>
      )}

      {/* Export CSV */}
      <div className="flex items-center gap-3">
        <a
          href={getExportCsvUrl()}
          download
          className="px-4 py-2 rounded-lg text-xs font-medium inline-flex items-center gap-1.5"
          style={{ background: "rgba(59,130,246,0.15)", border: "1px solid rgba(59,130,246,0.3)", color: "#60a5fa" }}
        >
          📊 导出本月 CSV 报告
        </a>
        <span className="text-[10px]" style={{ color: "var(--text-secondary)" }}>
          包含每日用量明细和费用汇总
        </span>
      </div>

      {/* Preset Modes */}
      {Object.keys(presets).length > 0 && (
        <div className="p-4 rounded-xl border space-y-3" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
          <h3 className="text-sm font-medium">快捷模式</h3>
          <div className="grid grid-cols-3 gap-2">
            {Object.entries(presets).map(([key, p]) => (
              <button
                key={key}
                disabled={presetApplying !== null}
                onClick={async () => {
                  setPresetApplying(key);
                  setTokenMsg(null);
                  try {
                    const res = await applyTokenPreset(key);
                    setTokenMsg(res.message);
                    await loadAll();
                  } catch {
                    setTokenMsg("切换失败");
                  } finally {
                    setPresetApplying(null);
                  }
                }}
                className="p-3 rounded-xl text-left transition-all"
                style={{
                  border: activePreset === key ? "2px solid var(--accent)" : "1px solid var(--border-color)",
                  background: activePreset === key ? "rgba(139,92,246,0.1)" : "var(--bg-primary)",
                  opacity: presetApplying && presetApplying !== key ? 0.5 : 1,
                }}
              >
                <div className="text-sm font-medium mb-1">{p.name}</div>
                <div className="text-[10px] mb-1" style={{ color: "var(--text-secondary)" }}>{p.description}</div>
                <div className="text-xs font-mono" style={{ color: "rgb(167,139,250)" }}>{p.est_per_chat}/次</div>
                {activePreset === key && <div className="text-[10px] mt-1 text-green-400">当前</div>}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Stats summary */}
      {tokenBudget && (
        <div className="grid grid-cols-3 gap-3">
          <div className="p-3 rounded-xl text-center" style={{ background: "rgba(59,130,246,0.1)", border: "1px solid rgba(59,130,246,0.3)" }}>
            <div className="text-lg font-bold text-blue-400">{(tokenBudget.total_est_tokens / 1000).toFixed(0)}K</div>
            <div className="text-xs" style={{ color: "var(--text-secondary)" }}>全部开启/次</div>
          </div>
          <div className="p-3 rounded-xl text-center" style={{ background: "rgba(34,197,94,0.1)", border: "1px solid rgba(34,197,94,0.3)" }}>
            <div className="text-lg font-bold text-green-400">{(tokenBudget.active_est_tokens / 1000).toFixed(0)}K</div>
            <div className="text-xs" style={{ color: "var(--text-secondary)" }}>当前开启/次</div>
          </div>
          <div className="p-3 rounded-xl text-center" style={{ background: "rgba(249,115,22,0.1)", border: "1px solid rgba(249,115,22,0.3)" }}>
            <div className="text-lg font-bold text-orange-400">{(tokenBudget.saved_tokens / 1000).toFixed(0)}K</div>
            <div className="text-xs" style={{ color: "var(--text-secondary)" }}>已节省/次</div>
          </div>
        </div>
      )}

      {tokenMsg && (
        <div className="text-xs px-3 py-2 rounded-lg border border-green-800 bg-green-900/20 text-green-300">
          {tokenMsg}
        </div>
      )}

      {/* Feature toggles */}
      <h3 className="text-sm font-medium pt-2">逐项控制</h3>
      {tokenBudget?.features.map((f) => (
        <div
          key={f.id}
          className="p-4 rounded-xl border flex items-center justify-between gap-4"
          style={{
            borderColor: f.enabled ? "rgba(34,197,94,0.3)" : "var(--border-color)",
            background: f.enabled ? "rgba(34,197,94,0.05)" : "var(--bg-secondary)",
          }}
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="font-medium text-sm">{f.name}</span>
              <span className="text-xs px-2 py-0.5 rounded-full" style={{ background: "rgba(139,92,246,0.15)", color: "rgb(167,139,250)" }}>
                ~{(f.est_tokens_per_use / 1000).toFixed(0)}K/次
              </span>
            </div>
            <p className="text-xs" style={{ color: "var(--text-secondary)" }}>{f.description}</p>
          </div>
          <button
            disabled={tokenTogglingId === f.id}
            onClick={async () => {
              setTokenTogglingId(f.id);
              setTokenMsg(null);
              try {
                const res = await toggleTokenFeature(f.id, !f.enabled);
                setTokenMsg(res.message || (f.enabled ? "已关闭" : "已开启"));
                await loadAll();
              } catch {
                setTokenMsg("操作失败");
              } finally {
                setTokenTogglingId(null);
              }
            }}
            className="relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none"
            style={{
              background: f.enabled ? "#22c55e" : "#374151",
              opacity: tokenTogglingId === f.id ? 0.5 : 1,
              cursor: tokenTogglingId === f.id ? "wait" : "pointer",
            }}
          >
            <span
              className="inline-block h-4 w-4 rounded-full bg-white transition-transform"
              style={{ transform: f.enabled ? "translateX(22px)" : "translateX(4px)" }}
            />
          </button>
        </div>
      ))}

      {!tokenBudget && (
        <p className="text-sm text-center py-4" style={{ color: "var(--text-secondary)" }}>加载中...</p>
      )}
    </div>
  );
}
