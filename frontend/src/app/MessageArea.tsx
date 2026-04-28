"use client";

import {
  Bot,
  User,
  Code2,
  Monitor,
  Wifi,
  WifiOff,
  Search,
  Pencil,
  Terminal,
} from "lucide-react";
import { AGENT_MODE_META, TOOL_NAME_LABELS, fallbackToolLabel, standaloneNoticeStyle } from "./chat-constants";
import type { PermissionPrompt } from "./chat-constants";
import type {
  SkillConfig,
  SpeculationDiffEntry,
  SpeculationHunkSelection,
  SpeculationNotice,
  SpeculationRecord,
} from "@/types";
import type { ToolCallInfo } from "@/components/MessageRenderer";
import { MessageContent, ToolCallsPanel } from "@/components/MessageRenderer";
import { FileAttachment, parseFileAttachments } from "@/components/FileAttachment";
import { SpeculationPanel } from "@/components/SpeculationPanel";
import DiffViewer from "@/components/DiffViewer";

interface MessageAreaProps {
  messages: { role: string; content: string; _streaming_terminal?: boolean }[];
  streaming: boolean;
  streamContent: string;
  agentMode: string;
  localClients: { client_id: string }[];
  localDisconnectNotice: boolean;
  setLocalDisconnectNotice: (v: boolean) => void;
  skills: SkillConfig[];
  setInput: (v: string) => void;
  setAgentMode: (m: "flash" | "standard" | "pro" | "ultra" | "local") => void;
  editingIndex: number | null;
  setEditingIndex: (v: number | null) => void;
  editingContent: string;
  setEditingContent: (v: string) => void;
  handleEditSubmit: (i: number) => void;
  toolCalls: ToolCallInfo[];
  fileDiffs: import("@/components/DiffViewer").FileDiff[];
  pendingPermission: PermissionPrompt | null;
  resolvePendingPermission: (approve: boolean, alwaysAllow?: boolean) => void;
  agentStatuses: { id: string; status: string; task?: string; role?: string; tool_calls?: { tool: string }[]; result_preview?: string }[];
  lastUsage: { input_tokens?: number; output_tokens?: number; cost_usd?: number; tool_calls?: number; agents_spawned?: number } | null;
  speculationEnabled: boolean;
  speculationRecord: SpeculationRecord | null;
  speculationDiffs: SpeculationDiffEntry[] | null;
  speculationDiffLoading: boolean;
  speculationBusyAction: "refresh" | "accept" | "discard" | null;
  speculationNotice: SpeculationNotice | null;
  handleUseSuggestion: () => void;
  handleRefreshSpeculation: () => void;
  handleAcceptSpeculation: (paths?: string[], hunks?: SpeculationHunkSelection[]) => void;
  handleDiscardSpeculation: () => void;
  messagesEndRef: React.RefObject<HTMLDivElement>;
}

export function MessageArea({
  messages,
  streaming,
  streamContent,
  agentMode,
  localClients,
  localDisconnectNotice,
  setLocalDisconnectNotice,
  skills,
  setInput,
  setAgentMode,
  editingIndex,
  setEditingIndex,
  editingContent,
  setEditingContent,
  handleEditSubmit,
  toolCalls,
  fileDiffs,
  pendingPermission,
  resolvePendingPermission,
  agentStatuses,
  lastUsage,
  speculationEnabled,
  speculationRecord,
  speculationDiffs,
  speculationDiffLoading,
  speculationBusyAction,
  speculationNotice,
  handleUseSuggestion,
  handleRefreshSpeculation,
  handleAcceptSpeculation,
  handleDiscardSpeculation,
  messagesEndRef,
}: MessageAreaProps) {
  return (
    <div className="flex-1 overflow-y-auto px-6 py-4 scrollbar-thin">
      {localDisconnectNotice && (
        <div className="mx-auto max-w-3xl mb-3 px-4 py-2.5 rounded-lg flex items-center gap-2 text-sm animate-pulse"
          style={{ background: "rgba(239,68,68,0.1)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.3)" }}>
          <WifiOff className="w-4 h-4 flex-shrink-0" />
          <span>本地客户端已断开连接，请检查 <code className="font-mono text-xs">local_client.py</code> 是否还在运行。</span>
          <button onClick={() => setLocalDisconnectNotice(false)} className="ml-auto text-xs opacity-60 hover:opacity-100">✕</button>
        </div>
      )}
      {messages.length === 0 && !streaming && (
        <div className="flex flex-col items-center justify-center h-full gap-4">
          <div className="w-16 h-16 rounded-2xl flex items-center justify-center" style={{ background: "var(--accent-light)" }}>
            <Bot className="w-8 h-8" style={{ color: "var(--accent)" }} />
          </div>
          <h2 className="text-xl font-semibold">天工流</h2>
          <p className="text-sm max-w-md text-center" style={{ color: "var(--text-secondary)" }}>
            AI Super Agent — 搜索、编码、创作，一气呵成
          </p>
          {agentMode === "local" && localClients.length === 0 && (
            <div className="mt-2 px-4 py-2 rounded-lg text-xs" style={{ background: "rgba(239,68,68,0.1)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.3)" }}>
              本地模式需要先连接客户端。请先在你的电脑上运行 <code className="font-mono">python local_client.py</code>。
            </div>
          )}

          <div className="flex gap-3 mt-2">
            {([
              { mode: "flash", desc: AGENT_MODE_META.flash.desc, icon: "Z" },
              { mode: "standard", desc: AGENT_MODE_META.standard.desc, icon: "S" },
              { mode: "pro", desc: AGENT_MODE_META.pro.desc, icon: "P" },
              { mode: "ultra", desc: AGENT_MODE_META.ultra.desc, icon: "U" },
              { mode: "local", desc: AGENT_MODE_META.local.desc, icon: "L" },
            ] as const).map((m) => (
              <button
                key={m.mode}
                onClick={() => setAgentMode(m.mode)}
                className="flex flex-col items-center gap-1 px-3 py-2 rounded-xl border transition-colors hover:opacity-80"
                style={{
                  borderColor: agentMode === m.mode ? "var(--accent)" : "var(--border-color)",
                  background: agentMode === m.mode ? "var(--accent-light)" : "var(--bg-secondary)",
                  color: "var(--text-primary)",
                }}
              >
                <span className="text-xs font-bold" style={{ color: "var(--accent)" }}>{m.icon}</span>
                <span className="text-xs font-medium">{AGENT_MODE_META[m.mode].label}</span>
                <span className="text-[10px]" style={{ color: "var(--text-secondary)" }}>{m.desc}</span>
              </button>
            ))}
          </div>

          <div className="flex flex-wrap gap-2 mt-3 max-w-lg justify-center">
            {skills.map((s) => (
              <button
                key={s.name}
                onClick={() => setInput(`使用 ${s.display_name} 技能`)}
                className="px-3 py-1.5 rounded-full text-xs font-medium cursor-pointer transition-opacity hover:opacity-80"
                style={{ background: "var(--accent-light)", color: "var(--accent)" }}
                title={s.description || s.display_name}
              >
                {s.display_name}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-2 mt-4 max-w-lg justify-center">
            {[
              { label: "创建一个网页应用", icon: "G" },
              { label: "用 Python 分析数据", icon: "D" },
              { label: "撰写一份研究报告", icon: "R" },
              { label: "制作一份演示文稿", icon: "P" },
            ].map((s) => (
              <button
                key={s.label}
                onClick={() => setInput(s.label)}
                className="px-3 py-2 rounded-xl text-xs border transition-colors hover:opacity-80"
                style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)", color: "var(--text-primary)" }}
              >
                {s.icon} {s.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {messages.map((msg, i) => {
        const isUser = msg.role === "user";
        const { text, attachments } = parseFileAttachments(msg.content);

        return (
          <div key={i} className="group flex gap-3 mb-5 max-w-3xl mx-auto">
            <div
              className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 mt-1"
              style={{ background: isUser ? "var(--user-bubble)" : "var(--assistant-bubble)" }}
            >
              {isUser ? (
                <User className="w-3.5 h-3.5" style={{ color: "var(--user-text)" }} />
              ) : (
                <Bot className="w-3.5 h-3.5" style={{ color: "var(--accent)" }} />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 text-xs font-medium mb-1" style={{ color: "var(--text-secondary)" }}>
                {isUser ? "你" : "天工流"}
                {isUser && !streaming && editingIndex !== i && (
                  <button
                    onClick={() => { setEditingIndex(i); setEditingContent(msg.content); }}
                    className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-white/10"
                    title="编辑消息"
                  >
                    <Pencil className="w-3 h-3" />
                  </button>
                )}
              </div>
              {editingIndex === i ? (
                <div className="flex flex-col gap-2">
                  <textarea
                    value={editingContent}
                    onChange={(e) => setEditingContent(e.target.value)}
                    className="w-full p-2 text-sm rounded-lg border outline-none resize-none"
                    style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)", minHeight: "60px" }}
                    autoFocus
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleEditSubmit(i)}
                      className="px-3 py-1 text-xs rounded-lg"
                      style={{ background: "var(--accent)", color: "#fff" }}
                    >
                      保存并重新发送
                    </button>
                    <button
                      onClick={() => { setEditingIndex(null); setEditingContent(""); }}
                      className="px-3 py-1 text-xs rounded-lg border"
                      style={{ borderColor: "var(--border-color)", color: "var(--text-secondary)" }}
                    >
                      取消
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  {attachments.length > 0 && (
                    <div className="flex flex-wrap gap-2 mb-2">
                      {attachments.map((a, j) => (
                        <FileAttachment key={j} filename={a.filename} size={a.size} />
                      ))}
                    </div>
                  )}
                  {(msg as { _streaming_terminal?: boolean })._streaming_terminal ? (
                    <div className="rounded-lg overflow-hidden border" style={{ borderColor: "var(--border-color)" }}>
                      <div className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium" style={{ background: "rgba(0,0,0,0.4)", color: "var(--text-secondary)" }}>
                        <Terminal className="w-3 h-3" /> Terminal Output
                      </div>
                      <pre className="p-3 text-xs leading-relaxed overflow-x-auto whitespace-pre-wrap font-mono" style={{ background: "rgba(0,0,0,0.55)", color: "#4ade80", maxHeight: "400px", overflowY: "auto" }}>
                        {text.replace(/^```\n?/, "").replace(/\n?```$/, "")}
                      </pre>
                    </div>
                  ) : (
                    <MessageContent content={text} isUser={isUser} />
                  )}
                </>
              )}
            </div>
          </div>
        );
      })}

      {toolCalls.length > 0 && (
        <ToolCallsPanel toolCalls={toolCalls} />
      )}

      {fileDiffs.length > 0 && (
        <div className="max-w-3xl mx-auto mb-4">
          <DiffViewer diffs={fileDiffs} />
        </div>
      )}

      {pendingPermission && (
        <div className="max-w-3xl mx-auto mb-4 p-4 rounded-xl border" style={{ background: "rgba(245,158,11,0.10)", borderColor: "rgba(245,158,11,0.35)" }}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-amber-300">
                需要你确认工具权限：{TOOL_NAME_LABELS[pendingPermission.tool] || fallbackToolLabel(pendingPermission.tool)}
              </div>
              <div className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>
                {pendingPermission.reason || "该操作需要确认后继续。"}
              </div>
              {pendingPermission.input && (
                <pre className="mt-2 max-h-24 overflow-auto whitespace-pre-wrap text-xs rounded-lg p-2" style={{ background: "rgba(0,0,0,0.25)", color: "var(--text-secondary)" }}>
                  {pendingPermission.input}
                </pre>
              )}
            </div>
            <div className="flex flex-col gap-2 flex-shrink-0">
              <button
                onClick={() => resolvePendingPermission(false)}
                className="px-3 py-1.5 rounded-lg text-xs border"
                style={{ borderColor: "var(--border-color)", color: "var(--text-secondary)" }}
              >
                拒绝
              </button>
              <button
                onClick={() => resolvePendingPermission(true)}
                className="px-3 py-1.5 rounded-lg text-xs text-white bg-amber-500 hover:bg-amber-400"
              >
                允许
              </button>
              <button
                onClick={() => resolvePendingPermission(true, true)}
                className="px-3 py-1.5 rounded-lg text-xs text-white"
                style={{ background: "#22c55e" }}
              >
                总是允许
              </button>
            </div>
          </div>
        </div>
      )}

      {agentStatuses.length > 0 && (
        <div className="max-w-3xl mx-auto mb-4 p-3 rounded-xl border" style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}>
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs font-medium" style={{ color: "var(--accent)" }}>
              <Code2 className="w-3.5 h-3.5 inline mr-1" />多智能体协作
            </div>
            <div className="text-[10px]" style={{ color: "var(--text-secondary)" }}>
              已完成 {agentStatuses.filter(a => a.status === "completed").length}/{agentStatuses.length}
            </div>
          </div>
          {/* Progress bar */}
          <div className="w-full h-1 rounded-full mb-2" style={{ background: "var(--border-color)" }}>
            <div
              className="h-1 rounded-full transition-all duration-500"
              style={{
                width: `${(agentStatuses.filter(a => a.status === "completed").length / Math.max(agentStatuses.length, 1)) * 100}%`,
                background: "var(--accent)",
              }}
            />
          </div>
          <div className="space-y-2">
            {agentStatuses.map((a) => {
              const roleIcons: Record<string, string> = { planner: "🧠", searcher: "🔍", researcher: "📚", coder: "💻", writer: "✍️", analyst: "📊", synthesizer: "🔗" };
              const icon = roleIcons[a.role || a.id] || (a.id === "planner" ? "🧠" : a.id === "synthesizer" ? "🔗" : "🤖");
              return (
                <div key={a.id} className="p-2 rounded-lg" style={{ background: a.status === "running" ? "rgba(var(--accent-rgb, 99,102,241), 0.08)" : "transparent" }}>
                  <div className="flex items-center gap-2 text-xs">
                    <span className="text-sm">{icon}</span>
                    <span
                      className={`w-2 h-2 rounded-full flex-shrink-0 ${
                        a.status === "completed" ? "bg-green-400"
                        : a.status === "running" ? "bg-yellow-400 animate-pulse"
                        : a.status === "failed" ? "bg-red-400"
                        : a.status === "planning" ? "bg-blue-400 animate-pulse"
                        : "bg-gray-400"
                      }`}
                    />
                    <span className="font-medium" style={{ color: "var(--text-primary)" }}>{a.id}</span>
                    {a.role && <span className="px-1.5 py-0.5 rounded text-[10px]" style={{ background: "var(--border-color)", color: "var(--text-secondary)" }}>{a.role}</span>}
                    <span className="flex-1 truncate" style={{ color: "var(--text-secondary)" }}>{a.task || a.status}</span>
                  </div>
                  {a.tool_calls && a.tool_calls.length > 0 && (
                    <div className="mt-1 ml-7 flex flex-wrap gap-1">
                      {a.tool_calls.map((tc, i) => (
                        <span key={i} className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "rgba(var(--accent-rgb, 99,102,241), 0.15)", color: "var(--accent)" }}>🔧 {tc.tool}</span>
                      ))}
                    </div>
                  )}
                  {a.result_preview && (
                    <div className="mt-1 ml-7 text-[10px] truncate" style={{ color: "var(--text-secondary)" }}>{a.result_preview}</div>
                  )}
                </div>
              );
            })}
          </div>
          {/* Cost info */}
          {lastUsage && !streaming && (
            <div className="mt-2 pt-2 flex gap-3 text-[10px]" style={{ borderTop: "1px solid var(--border-color)", color: "var(--text-secondary)" }}>
              {lastUsage.agents_spawned ? <span>🤖 {lastUsage.agents_spawned} 个智能体</span> : null}
              {lastUsage.tool_calls ? <span>🔧 {lastUsage.tool_calls} 次工具调用</span> : null}
              {lastUsage.input_tokens ? <span>📥 输入 {lastUsage.input_tokens}</span> : null}
              {lastUsage.output_tokens ? <span>📤 输出 {lastUsage.output_tokens}</span> : null}
              {lastUsage.cost_usd ? <span>💰 ${lastUsage.cost_usd.toFixed(4)}</span> : null}
            </div>
          )}
        </div>
      )}

      {speculationEnabled && !speculationRecord && speculationNotice ? (
        <div
          className="max-w-3xl mx-auto mb-4 p-3 rounded-xl border"
          style={{
            background: standaloneNoticeStyle(speculationNotice.kind).background,
            borderColor: standaloneNoticeStyle(speculationNotice.kind).borderColor,
          }}
        >
          <div className="text-xs font-semibold" style={{ color: standaloneNoticeStyle(speculationNotice.kind).color }}>
            {speculationNotice.title}
          </div>
          {speculationNotice.detail ? (
            <div className="mt-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
              {speculationNotice.detail}
            </div>
          ) : null}
        </div>
      ) : null}

      {speculationEnabled && (
        <SpeculationPanel
          record={speculationRecord}
          diffs={speculationDiffs}
          diffLoading={speculationDiffLoading}
          busyAction={speculationBusyAction}
          notice={speculationNotice}
          onUseSuggestion={handleUseSuggestion}
          onRefresh={handleRefreshSpeculation}
          onAccept={handleAcceptSpeculation}
          onDiscard={handleDiscardSpeculation}
        />
      )}

      {streaming && streamContent && (
        <div className="flex gap-3 mb-5 max-w-3xl mx-auto">
          <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 mt-1" style={{ background: "var(--assistant-bubble)" }}>
            <Bot className="w-3.5 h-3.5" style={{ color: "var(--accent)" }} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-xs font-medium mb-1" style={{ color: "var(--text-secondary)" }}>天工流</div>
            <MessageContent content={streamContent} />
          </div>
        </div>
      )}

      {streaming && !streamContent && agentStatuses.length === 0 && toolCalls.length === 0 && (
        <div className="flex gap-3 mb-5 max-w-3xl mx-auto">
          <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 mt-1" style={{ background: "var(--assistant-bubble)" }}>
            <Bot className="w-3.5 h-3.5" style={{ color: "var(--accent)" }} />
          </div>
          <div className="flex items-center gap-1.5 py-2">
            <div className="w-2 h-2 rounded-full typing-dot" style={{ background: "var(--accent)" }} />
            <div className="w-2 h-2 rounded-full typing-dot" style={{ background: "var(--accent)" }} />
            <div className="w-2 h-2 rounded-full typing-dot" style={{ background: "var(--accent)" }} />
          </div>
        </div>
      )}

      <div ref={messagesEndRef} />
    </div>
  );
}
