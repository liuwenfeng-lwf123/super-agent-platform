"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Check,
  ChevronDown,
  Clock3,
  FileCode2,
  GitBranch,
  RefreshCw,
  Sparkles,
  Wand2,
  Wrench,
  X,
} from "lucide-react";
import type { SpeculationDiffEntry, SpeculationHunkSelection, SpeculationNotice, SpeculationRecord } from "@/types";

interface SpeculationPanelProps {
  record: SpeculationRecord | null;
  diffs?: SpeculationDiffEntry[] | null;
  diffLoading?: boolean;
  busyAction?: "refresh" | "accept" | "discard" | null;
  notice?: SpeculationNotice | null;
  onUseSuggestion: () => void;
  onRefresh: () => void;
  onAccept: (paths?: string[], hunks?: SpeculationHunkSelection[]) => void;
  onDiscard: () => void;
}

function displayStatus(status: string) {
  const labels: Record<string, string> = {
    pending: "等待中",
    running: "运行中",
    completed: "已完成",
    consumed: "已采纳",
    partially_accepted: "部分接受",
    accepted: "已接受",
    cancelled: "已取消",
    cleared: "已清除",
  };
  return labels[status] || status;
}

function displayExecutionMode(mode: string) {
  const labels: Record<string, string> = {
    tool_agent: "工具代理",
    speculative_agent: "推测代理",
    local: "本地模式",
    standard: "标准模式",
  };
  return labels[mode] || mode;
}

function displayChangeStatus(status: string) {
  const labels: Record<string, string> = {
    added: "新增",
    modified: "已修改",
    deleted: "已删除",
    conflict: "冲突",
  };
  return labels[status] || status;
}

function statusStyle(status: string) {
  if (status === "accepted") {
    return { background: "rgba(34,197,94,0.12)", color: "#22c55e", border: "1px solid rgba(34,197,94,0.25)" };
  }
  if (status === "partially_accepted") {
    return { background: "rgba(16,185,129,0.12)", color: "#10b981", border: "1px solid rgba(16,185,129,0.25)" };
  }
  if (status === "completed" || status === "consumed") {
    return { background: "rgba(99,102,241,0.12)", color: "var(--accent)", border: "1px solid rgba(99,102,241,0.22)" };
  }
  if (status === "running" || status === "pending") {
    return { background: "rgba(234,179,8,0.12)", color: "#eab308", border: "1px solid rgba(234,179,8,0.22)" };
  }
  if (status === "cancelled" || status === "cleared") {
    return { background: "rgba(148,163,184,0.12)", color: "#94a3b8", border: "1px solid rgba(148,163,184,0.22)" };
  }
  return { background: "rgba(239,68,68,0.12)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.22)" };
}

export function SpeculationPanel({
  record,
  diffs = null,
  diffLoading = false,
  busyAction = null,
  notice = null,
  onUseSuggestion,
  onRefresh,
  onAccept,
  onDiscard,
}: SpeculationPanelProps) {
  const changes = useMemo(() => record?.changes || [], [record?.changes]);
  const toolCalls = useMemo(() => record?.tool_calls || [], [record?.tool_calls]);

  const selectablePaths = useMemo(
    () => changes.filter((change) => !change.conflict).map((change) => change.path),
    [changes]
  );
  const [selectedPaths, setSelectedPaths] = useState<string[]>(selectablePaths);
  const [selectedHunks, setSelectedHunks] = useState<Record<string, string[]>>({});

  useEffect(() => {
    setSelectedPaths(selectablePaths);
    setSelectedHunks({});
  }, [record?.thread_id, record?.created_at, record?.status, selectablePaths]);

  const hasConflicts = changes.some((change) => change.conflict);
  const selectedHunkSelections = useMemo(
    () => Object.entries(selectedHunks)
      .filter(([, ids]) => ids.length > 0)
      .map(([path, ids]) => ({ path, ids })),
    [selectedHunks]
  );
  const selectedHunkCountByPath = useMemo(
    () => Object.fromEntries(Object.entries(selectedHunks).map(([path, ids]) => [path, ids.length])),
    [selectedHunks]
  );
  const selectedHunkCount = useMemo(
    () => Object.values(selectedHunks).reduce((total, ids) => total + ids.length, 0),
    [selectedHunks]
  );

  if (!record) return null;

  const canAccept = ["completed", "consumed", "partially_accepted"].includes(record.status)
    && (selectedPaths.length > 0 || selectedHunkSelections.length > 0);

  const togglePath = (path: string) => {
    setSelectedPaths((prev) => (prev.includes(path) ? prev.filter((item) => item !== path) : [...prev, path]));
    setSelectedHunks((prev) => {
      if (!(path in prev)) return prev;
      const next = { ...prev };
      delete next[path];
      return next;
    });
  };

  const toggleHunk = (path: string, hunkId: string) => {
    setSelectedPaths((prev) => prev.filter((item) => item !== path));
    setSelectedHunks((prev) => {
      const current = prev[path] || [];
      const nextIds = current.includes(hunkId)
        ? current.filter((item) => item !== hunkId)
        : [...current, hunkId];
      const next = { ...prev };
      if (nextIds.length > 0) {
        next[path] = nextIds;
      } else {
        delete next[path];
      }
      return next;
    });
  };

  const allSelected = selectablePaths.length > 0
    && selectablePaths.every((path) => selectedPaths.includes(path))
    && selectedHunkSelections.length === 0;
  const remainingChangesCount = changes.length;
  const noticeStyle = notice?.kind === "success"
    ? { background: "rgba(34,197,94,0.10)", borderColor: "rgba(34,197,94,0.30)", color: "#22c55e" }
    : notice?.kind === "error"
    ? { background: "rgba(239,68,68,0.10)", borderColor: "rgba(239,68,68,0.30)", color: "#ef4444" }
    : { background: "rgba(99,102,241,0.10)", borderColor: "rgba(99,102,241,0.22)", color: "var(--accent)" };

  return (
    <div
      className="max-w-3xl mx-auto mb-4 p-4 rounded-2xl border"
      style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}
    >
      <div className="flex flex-wrap items-center gap-2 justify-between mb-3">
        <div className="flex items-center gap-2 min-w-0">
          <Sparkles className="w-4 h-4 flex-shrink-0" style={{ color: "var(--accent)" }} />
          <div className="min-w-0">
            <div className="text-sm font-semibold truncate" style={{ color: "var(--text-primary)" }}>
              推测分支
            </div>
            <div className="text-xs truncate" style={{ color: "var(--text-secondary)" }}>
              {record.suggestion}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          <span className="px-2 py-1 rounded-full text-[11px] font-medium" style={statusStyle(record.status)}>
            {displayStatus(record.status)}
          </span>
          <span className="px-2 py-1 rounded-full text-[11px] font-medium" style={{ background: "var(--bg-primary)", color: "var(--text-secondary)", border: "1px solid var(--border-color)" }}>
            {displayExecutionMode(record.execution_mode)}
          </span>
        </div>
      </div>

      <div className="flex flex-wrap gap-2 mb-3 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        <span className="px-2 py-1 rounded-full border" style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}>
          剩余 {remainingChangesCount} 项变更
        </span>
        <span className="px-2 py-1 rounded-full border" style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}>
          已选 {selectedPaths.length} 个文件
        </span>
        <span className="px-2 py-1 rounded-full border" style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}>
          已选 {selectedHunkCount} 个代码块
        </span>
      </div>

      {notice ? (
        <div
          className="mb-3 rounded-xl border p-3"
          style={{ background: noticeStyle.background, borderColor: noticeStyle.borderColor }}
        >
          <div className="text-xs font-semibold" style={{ color: noticeStyle.color }}>
            {notice.title}
          </div>
          {notice.detail ? (
            <div className="mt-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
              {notice.detail}
            </div>
          ) : null}
          {notice.applied && notice.applied.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {notice.applied.slice(0, 6).map((item, index) => (
                <span
                  key={`${item.path}-${index}`}
                  className="px-2 py-1 rounded-full text-[11px]"
                  style={{ background: "var(--bg-primary)", color: "var(--text-secondary)", border: "1px solid var(--border-color)" }}
                >
                  {item.path}{item.hunks && item.hunks.length > 0 ? `（${item.hunks.length} 个代码块）` : ""}
                </span>
              ))}
            </div>
          ) : null}
          {typeof notice.remainingCount === "number" ? (
            <div className="mt-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
              还剩 {notice.remainingCount} 项推测性变更。
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2 mb-3">
        <button
          onClick={onUseSuggestion}
          className="px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 hover:opacity-85"
          style={{ background: "var(--accent)", color: "#fff" }}
        >
          <Wand2 className="w-3.5 h-3.5" /> 使用建议
        </button>
        <button
          onClick={onRefresh}
          disabled={busyAction !== null}
          className="px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 border disabled:opacity-50"
          style={{ borderColor: "var(--border-color)", color: "var(--text-secondary)" }}
        >
          <RefreshCw className={`w-3.5 h-3.5 ${busyAction === "refresh" ? "animate-spin" : ""}`} /> 刷新
        </button>
        <button
          onClick={() => onAccept(selectedPaths, selectedHunkSelections)}
          disabled={!canAccept || busyAction !== null}
          className="px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 disabled:opacity-50"
          style={{ background: "rgba(34,197,94,0.15)", color: "#22c55e" }}
        >
          <Check className="w-3.5 h-3.5" /> {allSelected ? "全部接受" : `接受所选（${selectedPaths.length + selectedHunkSelections.length}）`}
        </button>
        <button
          onClick={onDiscard}
          disabled={busyAction !== null}
          className="px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 border disabled:opacity-50"
          style={{ borderColor: "rgba(239,68,68,0.35)", color: "#ef4444" }}
        >
          <X className="w-3.5 h-3.5" /> 丢弃
        </button>
      </div>

      {record.status === "partially_accepted" ? (
        <div className="mb-3 text-[11px]" style={{ color: "#10b981" }}>
          这次推测已经有一部分被应用了。请先审阅下面剩余的变更，再决定是否继续接受。
        </div>
      ) : null}

      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-xl border p-3" style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}>
          <div className="flex items-center gap-1.5 text-xs font-medium mb-2" style={{ color: "var(--accent)" }}>
            <Clock3 className="w-3.5 h-3.5" /> 草稿
          </div>
          <div className="text-xs whitespace-pre-wrap leading-6" style={{ color: "var(--text-secondary)" }}>
            {record.draft || "推测草稿仍在生成中..."}
          </div>
          {record.error ? (
            <div className="mt-2 text-[11px] whitespace-pre-wrap" style={{ color: "#ef4444" }}>
              {record.error}
            </div>
          ) : null}
        </div>

        <div className="rounded-xl border p-3" style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}>
          <div className="flex items-center justify-between gap-2 mb-2">
            <div className="flex items-center gap-1.5 text-xs font-medium" style={{ color: "var(--accent)" }}>
              <GitBranch className="w-3.5 h-3.5" /> 影子变更
            </div>
            {selectablePaths.length > 0 ? (
              <div className="flex items-center gap-2 text-[11px]">
                <button
                  onClick={() => {
                    setSelectedPaths(selectablePaths);
                    setSelectedHunks({});
                  }}
                  className="hover:opacity-80"
                  style={{ color: "var(--accent)" }}
                >
                  全选
                </button>
                <button
                  onClick={() => {
                    setSelectedPaths([]);
                    setSelectedHunks({});
                  }}
                  className="hover:opacity-80"
                  style={{ color: "var(--text-secondary)" }}
                >
                  清空
                </button>
              </div>
            ) : null}
          </div>
          {changes.length === 0 ? (
            <div className="text-xs" style={{ color: "var(--text-secondary)" }}>
              暂时还没有捕获到文件变更。
            </div>
          ) : (
            <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
              {changes.map((change, index) => (
                <div
                  key={`${change.path}-${index}`}
                  className="flex items-start gap-2 px-2.5 py-2 rounded-lg"
                  style={{
                    background: change.conflict
                      ? "rgba(239,68,68,0.10)"
                      : selectedPaths.includes(change.path)
                      || (selectedHunkCountByPath[change.path] || 0) > 0
                      ? "rgba(99,102,241,0.10)"
                      : "var(--bg-secondary)",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={selectedPaths.includes(change.path)}
                    disabled={change.conflict}
                    onChange={() => togglePath(change.path)}
                    className="mt-0.5"
                  />
                  <FileCode2 className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" style={{ color: change.conflict ? "#ef4444" : "var(--accent)" }} />
                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-medium truncate" style={{ color: "var(--text-primary)" }}>
                      {change.path}
                    </div>
                    <div className="text-[11px] flex items-center gap-2 flex-wrap" style={{ color: "var(--text-secondary)" }}>
                      <span>{displayChangeStatus(change.status)}</span>
                      {typeof change.size === "number" ? <span>{change.size} 字节</span> : null}
                      {(selectedHunkCountByPath[change.path] || 0) > 0 ? <span>{selectedHunkCountByPath[change.path]} 个代码块</span> : null}
                      {change.conflict ? <span style={{ color: "#ef4444" }}>冲突</span> : null}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="mt-3 rounded-xl border p-3" style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}>
        <div className="flex items-center justify-between gap-2 mb-2">
          <div className="flex items-center gap-1.5 text-xs font-medium" style={{ color: "var(--accent)" }}>
            <FileCode2 className="w-3.5 h-3.5" /> 补丁预览
          </div>
          {diffLoading ? (
            <div className="flex items-center gap-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
              <RefreshCw className="w-3 h-3 animate-spin" /> 正在加载差异...
            </div>
          ) : null}
        </div>

        {!diffLoading && (!diffs || diffs.length === 0) ? (
          <div className="text-xs" style={{ color: "var(--text-secondary)" }}>
            暂时还没有可用的补丁预览。
          </div>
        ) : null}

        {diffs && diffs.length > 0 ? (
          <div className="space-y-2">
            {diffs.map((entry, index) => (
              <details
                key={`${entry.path}-${index}`}
                className="rounded-lg border overflow-hidden"
                style={{ borderColor: entry.conflict ? "rgba(239,68,68,0.35)" : "var(--border-color)" }}
              >
                <summary
                  className="list-none cursor-pointer px-3 py-2 flex items-center gap-2"
                  style={{ background: "var(--bg-secondary)", color: "var(--text-primary)" }}
                >
                  <ChevronDown className="w-3.5 h-3.5 flex-shrink-0" style={{ color: "var(--text-secondary)" }} />
                  <span className="text-xs font-medium truncate flex-1">{entry.path}</span>
                  <span className="text-[11px]" style={{ color: "var(--text-secondary)" }}>{displayChangeStatus(entry.status)}</span>
                  {entry.conflict ? <span className="text-[11px]" style={{ color: "#ef4444" }}>冲突</span> : null}
                </summary>
                <div className="px-3 py-2 border-t" style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}>
                  {entry.binary ? (
                    <div className="text-xs" style={{ color: "var(--text-secondary)" }}>
                      二进制文件暂不支持差异预览。
                    </div>
                  ) : entry.hunks.length > 0 ? (
                    <div className="space-y-2">
                      {entry.hunks.map((hunk) => (
                        <div
                          key={`${entry.path}-${hunk.id}`}
                          className="rounded-lg border p-2"
                          style={{
                            borderColor: (selectedHunks[entry.path] || []).includes(hunk.id) ? "var(--accent)" : "var(--border-color)",
                            background: "var(--bg-secondary)",
                          }}
                        >
                          <label className="flex items-center gap-2 text-[11px] cursor-pointer" style={{ color: "var(--text-primary)" }}>
                            <input
                              type="checkbox"
                              checked={(selectedHunks[entry.path] || []).includes(hunk.id)}
                              disabled={entry.conflict}
                              onChange={() => toggleHunk(entry.path, hunk.id)}
                            />
                            <span className="font-medium">{hunk.header}</span>
                          </label>
                          <pre
                            className="mt-2 text-[11px] leading-5 whitespace-pre-wrap overflow-x-auto rounded-lg p-2"
                            style={{ background: "var(--bg-primary)", color: "var(--text-secondary)" }}
                          >
                            {hunk.diff}
                          </pre>
                          {hunk.truncated ? (
                            <div className="mt-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
                              这个代码块的预览已被截断。
                            </div>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  ) : entry.diff ? (
                    <pre
                      className="text-[11px] leading-5 whitespace-pre-wrap overflow-x-auto rounded-lg p-3"
                      style={{ background: "var(--bg-secondary)", color: "var(--text-secondary)" }}
                    >
                      {entry.diff}
                    </pre>
                  ) : (
                    <div className="text-xs" style={{ color: "var(--text-secondary)" }}>
                      暂时没有可展示的文本差异。
                    </div>
                  )}
                  {entry.truncated ? (
                    <div className="mt-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
                      差异预览已被截断。
                    </div>
                  ) : null}
                </div>
              </details>
            ))}
          </div>
        ) : null}
      </div>

      {(record.tool_summary || toolCalls.length > 0 || record.accepted_at || hasConflicts) && (
        <div className="mt-3 rounded-xl border p-3" style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}>
          <div className="flex items-center gap-1.5 text-xs font-medium mb-2" style={{ color: "var(--accent)" }}>
            <Wrench className="w-3.5 h-3.5" /> 执行轨迹
          </div>
          {record.tool_summary ? (
            <div className="text-xs mb-2" style={{ color: "var(--text-secondary)" }}>
              {record.tool_summary}
            </div>
          ) : null}
          {toolCalls.length > 0 ? (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {toolCalls.map((toolCall, index) => (
                <span
                  key={`${toolCall.tool}-${index}`}
                  className="px-2 py-1 rounded-full text-[11px]"
                  style={{ background: "var(--bg-secondary)", color: "var(--text-secondary)", border: "1px solid var(--border-color)" }}
                >
                  {toolCall.tool}
                </span>
              ))}
            </div>
          ) : null}
          {hasConflicts ? (
            <div className="text-[11px] mb-1" style={{ color: "#ef4444" }}>
              有冲突的文件不能直接勾选，请先刷新后再决定是否接受。
            </div>
          ) : null}
          {record.accepted_at ? (
            <div className="text-[11px]" style={{ color: "#22c55e" }}>
              接受时间：{new Date(record.accepted_at).toLocaleString()}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
