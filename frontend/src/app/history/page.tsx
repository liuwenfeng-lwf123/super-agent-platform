"use client";

import { useState, useEffect, useCallback } from "react";
import { fetchFileHistory, fetchThreads } from "@/lib/api";
import { ArrowLeft, GitBranch, FilePlus, FileEdit, Search } from "lucide-react";

interface FileHistoryEntry {
  timestamp: string;
  path: string;
  action: "create" | "modify" | string;
  old_size: number;
  new_size: number;
  diff: string;
  source?: string;
  success?: boolean;
  client_id?: string;
}

export default function HistoryPage() {
  const [threads, setThreads] = useState<{ id: string; title?: string }[]>([]);
  const [selectedThread, setSelectedThread] = useState("");
  const [entries, setEntries] = useState<FileHistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [pathFilter, setPathFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const loadThreads = useCallback(async () => {
    try {
      const data = await fetchThreads();
      const list = Array.isArray(data) ? data : data?.threads || [];
      setThreads(list);
      if (list.length > 0 && !selectedThread) setSelectedThread(list[0].id);
    } catch { /* */ }
  }, [selectedThread]);

  useEffect(() => { loadThreads(); }, [loadThreads]);

  const loadHistory = useCallback(async () => {
    if (!selectedThread) return;
    setLoading(true);
    try {
      const data = await fetchFileHistory(selectedThread, pathFilter || undefined, 100);
      setEntries(Array.isArray(data?.entries) ? data.entries : []);
    } catch { setEntries([]); }
    setLoading(false);
  }, [selectedThread, pathFilter]);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  const toggle = (i: number) => {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  };

  const renderDiff = (diff: string) => {
    if (!diff) return <div className="text-xs italic" style={{ color: "var(--text-secondary)" }}>这条记录没有文件 diff。本地操作会记录动作和目标；只有 AI 写入沙盒文件时才会显示代码差异。</div>;
    return (
      <pre className="text-xs font-mono leading-5 overflow-x-auto">
        {diff.split("\n").map((line, i) => {
          let color = "var(--text-primary)";
          let bg = "transparent";
          if (line.startsWith("+") && !line.startsWith("+++")) { color = "#4ade80"; bg = "rgba(74,222,128,0.08)"; }
          else if (line.startsWith("-") && !line.startsWith("---")) { color = "#f87171"; bg = "rgba(248,113,113,0.08)"; }
          else if (line.startsWith("@@")) { color = "#60a5fa"; }
          else if (line.startsWith("---") || line.startsWith("+++")) { color = "var(--text-secondary)"; }
          return <div key={i} style={{ color, background: bg, paddingLeft: "0.5rem" }}>{line || " "}</div>;
        })}
      </pre>
    );
  };

  const reversed = [...entries].reverse();

  return (
    <div className="h-screen flex flex-col" style={{ background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-4 border-b" style={{ borderColor: "var(--border-color)" }}>
        <button onClick={() => window.location.href = "/"} className="p-2 rounded-lg hover:bg-white/5">
          <ArrowLeft size={18} />
        </button>
        <GitBranch size={22} style={{ color: "var(--accent)" }} />
        <h1 className="text-lg font-semibold">文件历史</h1>
        <div className="flex-1" />

        {/* Thread selector */}
        <select value={selectedThread} onChange={e => setSelectedThread(e.target.value)}
          className="px-3 py-1.5 rounded-lg border text-sm"
          style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}>
          {threads.map(t => (
            <option key={t.id} value={t.id}>{t.title || t.id.slice(0, 12)}</option>
          ))}
        </select>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div className="max-w-4xl mx-auto space-y-4">
          {/* Path filter */}
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input value={pathFilter} onChange={e => setPathFilter(e.target.value)}
              placeholder="按文件路径过滤..."
              className="w-full pl-10 pr-4 py-2.5 rounded-lg border text-sm"
              style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }} />
          </div>

          {/* Entries */}
          {loading ? (
            <div className="text-center py-12" style={{ color: "var(--text-secondary)" }}>加载中...</div>
          ) : reversed.length === 0 ? (
            <div className="text-center py-12" style={{ color: "var(--text-secondary)" }}>
              {selectedThread ? "该线程暂无文件变更记录" : "请选择一个线程"}
              {selectedThread && (
                <div className="text-xs mt-2" style={{ color: "var(--text-secondary)" }}>
                  这里显示 AI 在该会话里写入沙盒文件的历史，也会合并本地电脑工具操作记录。只聊天或只读取文件不会产生文件 diff。
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-2">
              <div className="text-sm" style={{ color: "var(--text-secondary)" }}>共 {reversed.length} 条变更</div>
              {reversed.map((e, i) => {
                const isExpanded = expanded.has(i);
                const isCreate = e.action === "create";
                const isLocal = e.source === "local" || e.action?.startsWith("local_");
                const actionLabel = isLocal
                  ? e.action.replace("local_list_files", "本地列文件").replace("local_open_app", "本地打开应用").replace("local_get_system_info", "本地系统信息")
                  : isCreate ? "创建" : "修改";
                return (
                  <div key={i} className="rounded-lg border overflow-hidden" style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}>
                    <button onClick={() => toggle(i)} className="w-full flex items-center gap-3 p-3 text-left hover:bg-white/3">
                      {isLocal
                        ? <Search size={16} className="text-blue-400 shrink-0" />
                        : isCreate
                        ? <FilePlus size={16} className="text-green-400 shrink-0" />
                        : <FileEdit size={16} className="text-yellow-400 shrink-0" />}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className={`text-xs px-1.5 py-0.5 rounded ${isLocal ? "bg-blue-500/20 text-blue-300" : isCreate ? "bg-green-500/20 text-green-300" : "bg-yellow-500/20 text-yellow-300"}`}>
                            {actionLabel}
                          </span>
                          <span className="text-sm font-mono truncate">{e.path}</span>
                        </div>
                        <div className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
                          {e.timestamp?.slice(0, 19)}
                          {isLocal ? ` · ${e.success ? "成功" : "失败"}${e.client_id ? ` · ${e.client_id.slice(0, 16)}` : ""}` : ` · ${e.old_size} → ${e.new_size} bytes`}
                        </div>
                      </div>
                      <span className="text-xs" style={{ color: "var(--text-secondary)" }}>{isExpanded ? "▼" : "▶"}</span>
                    </button>
                    {isExpanded && (
                      <div className="border-t px-3 py-2 overflow-auto max-h-80" style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}>
                        {renderDiff(e.diff)}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
