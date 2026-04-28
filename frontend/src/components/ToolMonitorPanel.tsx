"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, CheckCircle2, ChevronDown, Monitor, RefreshCw, Search, Wrench, XCircle } from "lucide-react";
import { fetchToolMonitorEvents } from "@/lib/api";
import type { ThreadListItem, ToolMonitorEvent, ToolMonitorResponse } from "@/types";

type ToolThread = NonNullable<ToolMonitorResponse["threads"]>[number];

type Props = {
  compact?: boolean;
  activeThreadId?: string | null;
  threads?: ThreadListItem[];
};

const sourceOptions = [
  { value: "", label: "全部来源" },
  { value: "agent", label: "AI 工具" },
  { value: "local", label: "本地电脑" },
];

const toolLabels: Record<string, string> = {
  web_search: "网页搜索",
  web_fetch: "读取网页",
  execute_python: "运行 Python",
  execute_javascript: "运行 JavaScript",
  execute_bash: "运行终端命令",
  read_file: "读取文件",
  write_file: "写入文件",
  list_files: "列出文件",
  calculate: "计算",
  get_current_time: "获取时间",
  local_list_files: "查看电脑文件",
  local_open_app: "打开电脑应用",
  local_execute_bash: "本地终端命令",
  local_execute_python: "本地 Python",
  local_read_file: "读取本地文件",
  local_write_file: "写入本地文件",
  local_get_system_info: "查看电脑信息",
};

const categoryLabels: Record<string, string> = {
  local: "本地电脑",
  search: "搜索",
  file: "文件",
  execution: "执行",
  utility: "工具",
  system: "系统",
  memory: "记忆",
  general: "通用",
};

function formatTime(value: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 19);
  return date.toLocaleString("zh-CN", { hour12: false });
}

function prettyJson(value: string) {
  if (!value) return "无";
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function toolIcon(event: ToolMonitorEvent) {
  if (event.source === "local" || event.category === "local") return Monitor;
  if (event.category === "search") return Search;
  return Wrench;
}

function threadTitle(threadId: string, threads: ThreadListItem[] = [], monitorThreads: ToolThread[] = []) {
  if (!threadId) return "全部会话";
  const thread = threads.find((item) => item.id === threadId);
  if (thread) return thread.title || threadId.slice(0, 8);
  const monitorThread = monitorThreads.find((item) => item.thread_id === threadId);
  if (monitorThread) return `会话 ${threadId.slice(0, 8)} · ${monitorThread.count} 次调用`;
  return `会话 ${threadId.slice(0, 8)}`;
}

export function ToolMonitorPanel({ compact = false, activeThreadId, threads = [] }: Props) {
  const [events, setEvents] = useState<ToolMonitorEvent[]>([]);
  const [monitorThreads, setMonitorThreads] = useState<ToolThread[]>([]);
  const [source, setSource] = useState("");
  const [selectedThreadId, setSelectedThreadId] = useState(activeThreadId || "");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (activeThreadId && !selectedThreadId) setSelectedThreadId(activeThreadId);
  }, [activeThreadId, selectedThreadId]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchToolMonitorEvents(200, source, selectedThreadId);
      setEvents(Array.isArray(data.events) ? data.events : []);
      setMonitorThreads(Array.isArray(data.threads) ? data.threads : []);
    } finally {
      setLoading(false);
    }
  }, [source, selectedThreadId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!autoRefresh) return;
    const timer = setInterval(load, 2500);
    return () => clearInterval(timer);
  }, [autoRefresh, load]);

  const selectableThreads = useMemo(() => {
    const seen = new Set<string>();
    const items: { id: string; label: string }[] = [{ id: "", label: "全部会话" }];
    for (const thread of threads) {
      if (seen.has(thread.id)) continue;
      seen.add(thread.id);
      items.push({ id: thread.id, label: thread.title || `会话 ${thread.id.slice(0, 8)}` });
    }
    for (const thread of monitorThreads) {
      if (seen.has(thread.thread_id)) continue;
      seen.add(thread.thread_id);
      items.push({ id: thread.thread_id, label: `会话 ${thread.thread_id.slice(0, 8)} · ${thread.count} 次调用` });
    }
    return items;
  }, [threads, monitorThreads]);

  const stats = useMemo(() => {
    const success = events.filter((event) => event.success).length;
    return { total: events.length, success, failed: events.length - success, local: events.filter((event) => event.source === "local").length };
  }, [events]);

  return (
    <div className="h-full flex flex-col" style={{ color: "var(--text-primary)" }}>
      <div className={compact ? "p-4 border-b" : "p-6 border-b"} style={{ borderColor: "var(--border-color)" }}>
        <div className="flex items-start gap-3">
          <div className="p-2 rounded-xl" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>
            <Activity size={compact ? 18 : 22} />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="font-semibold">AI 工具调用后台</h2>
            <p className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>按会话查看 AI 调用了什么工具、传了什么参数、返回了什么结果。</p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2 mt-4">
          {[
            { label: "调用", value: stats.total },
            { label: "成功", value: stats.success },
            { label: "失败", value: stats.failed },
            { label: "本地", value: stats.local },
          ].map((item) => (
            <div key={item.label} className="rounded-lg border p-3" style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)" }}>
              <div className="text-[11px]" style={{ color: "var(--text-secondary)" }}>{item.label}</div>
              <div className="text-lg font-bold">{item.value}</div>
            </div>
          ))}
        </div>

        <div className="grid gap-2 mt-4">
          <label className="text-xs" style={{ color: "var(--text-secondary)" }}>选择会话</label>
          <div className="relative">
            <select value={selectedThreadId} onChange={(event) => setSelectedThreadId(event.target.value)} className="w-full appearance-none px-3 py-2 pr-8 rounded-lg border text-sm outline-none" style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}>
              {selectableThreads.map((thread) => <option key={thread.id || "all"} value={thread.id}>{thread.label}</option>)}
            </select>
            <ChevronDown className="w-4 h-4 absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "var(--text-secondary)" }} />
          </div>
          <div className="flex gap-2">
            <select value={source} onChange={(event) => setSource(event.target.value)} className="min-w-0 flex-1 px-3 py-2 rounded-lg border text-sm outline-none" style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}>
              {sourceOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
            <button onClick={() => setAutoRefresh((value) => !value)} className="px-3 py-2 rounded-lg border text-xs" style={{ borderColor: autoRefresh ? "var(--accent)" : "var(--border-color)", color: autoRefresh ? "var(--accent)" : "var(--text-secondary)" }}>{autoRefresh ? "自动开" : "自动关"}</button>
            <button onClick={load} className="px-3 py-2 rounded-lg border" style={{ borderColor: "var(--border-color)", color: "var(--text-secondary)" }}><RefreshCw size={14} className={loading ? "animate-spin" : ""} /></button>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-4 space-y-3">
        {selectedThreadId && <div className="text-xs" style={{ color: "var(--text-secondary)" }}>当前：{threadTitle(selectedThreadId, threads, monitorThreads)}</div>}
        {events.length === 0 ? (
          <div className="h-48 flex items-center justify-center text-sm text-center" style={{ color: "var(--text-secondary)" }}>这个会话还没有工具调用。你可以让 AI 搜索、列文件、打开应用后再看。</div>
        ) : events.map((event) => {
          const Icon = toolIcon(event);
          const isOpen = Boolean(expanded[event.event_id]);
          return (
            <div key={event.event_id} className="rounded-xl border overflow-hidden" style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)" }}>
              <button onClick={() => setExpanded((prev) => ({ ...prev, [event.event_id]: !prev[event.event_id] }))} className="w-full text-left p-3 hover:bg-white/5 transition-colors">
                <div className="flex gap-3">
                  <div className="p-2 rounded-lg h-fit" style={{ background: "var(--bg-secondary)", color: "var(--accent)" }}><Icon size={15} /></div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-sm">{toolLabels[event.tool] || event.tool}</span>
                      <span className="text-[11px] px-2 py-0.5 rounded bg-blue-500/15 text-blue-300">{categoryLabels[event.category] || event.category || "通用"}</span>
                      {event.success ? <span className="inline-flex items-center gap-1 text-[11px] text-green-400"><CheckCircle2 size={12} />成功</span> : <span className="inline-flex items-center gap-1 text-[11px] text-red-400"><XCircle size={12} />失败</span>}
                    </div>
                    <div className="text-[11px] mt-1" style={{ color: "var(--text-secondary)" }}>{formatTime(event.timestamp)}{event.thread_id ? ` · 会话 ${event.thread_id.slice(0, 8)}` : ""}</div>
                    <div className="text-xs mt-2 line-clamp-2" style={{ color: "var(--text-secondary)" }}>{prettyJson(event.input)}</div>
                  </div>
                </div>
              </button>
              {isOpen && (
                <div className="border-t p-3 grid gap-3" style={{ borderColor: "var(--border-color)" }}>
                  <div>
                    <div className="text-xs font-medium mb-1" style={{ color: "var(--text-secondary)" }}>输入参数</div>
                    <pre className="text-xs rounded-lg p-3 overflow-auto max-h-56 whitespace-pre-wrap" style={{ background: "#09090f" }}>{prettyJson(event.input)}</pre>
                  </div>
                  <div>
                    <div className="text-xs font-medium mb-1" style={{ color: "var(--text-secondary)" }}>返回结果</div>
                    <pre className="text-xs rounded-lg p-3 overflow-auto max-h-72 whitespace-pre-wrap" style={{ background: "#09090f" }}>{prettyJson(event.output)}</pre>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
