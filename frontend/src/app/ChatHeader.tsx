"use client";

import {
  Menu,
  Monitor,
  Wifi,
  WifiOff,
  RefreshCw,
  Download,
  StopCircle,
  Activity,
  Sparkles,
  Settings,
} from "lucide-react";
import { AGENT_MODE_META } from "./chat-constants";
import type { ModelConfig, ThreadListItem, LocalClient } from "@/types";

interface ChatHeaderProps {
  threads: ThreadListItem[];
  activeThreadId: string | null;
  agentMode: "flash" | "standard" | "pro" | "ultra" | "local";
  setAgentMode: (m: "flash" | "standard" | "pro" | "ultra" | "local") => void;
  sidebarOpen: boolean;
  setSidebarOpen: (v: boolean) => void;
  localClients: LocalClient[];
  showLocalPanel: boolean;
  setShowLocalPanel: (v: boolean) => void;
  messages: { role: string; content: string }[];
  streaming: boolean;
  models: ModelConfig[];
  selectedModel: string;
  setSelectedModel: (v: string) => void;
  showMonitorPanel: boolean;
  setShowMonitorPanel: (v: boolean | ((prev: boolean) => boolean)) => void;
  speculationEnabled: boolean;
  toggleSpeculation: () => void;
  handleRegenerate: () => void;
  handleExport: () => void;
  handleStop: () => void;
}

export function ChatHeader({
  threads,
  activeThreadId,
  agentMode,
  setAgentMode,
  sidebarOpen,
  setSidebarOpen,
  localClients,
  showLocalPanel,
  setShowLocalPanel,
  messages,
  streaming,
  models,
  selectedModel,
  setSelectedModel,
  showMonitorPanel,
  setShowMonitorPanel,
  speculationEnabled,
  toggleSpeculation,
  handleRegenerate,
  handleExport,
  handleStop,
}: ChatHeaderProps) {
  return (
    <header className="h-14 flex items-center justify-between px-6 border-b" style={{ borderColor: "var(--border-color)" }}>
      <div className="flex items-center gap-3">
        <button
          onClick={() => setSidebarOpen(!sidebarOpen)}
          className="md:hidden p-1 rounded-lg hover:opacity-80"
          style={{ color: "var(--text-secondary)" }}
        >
          <Menu className="w-5 h-5" />
        </button>
        <h1 className="text-sm font-semibold truncate max-w-[200px]">
          {activeThreadId
            ? threads.find((t) => t.id === activeThreadId)?.title || "对话"
            : "新对话"}
        </h1>
        <div className="flex rounded-lg overflow-hidden border" style={{ borderColor: "var(--border-color)" }}>
          {(["flash", "standard", "pro", "ultra", "local"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setAgentMode(m)}
              className="px-2 py-1 text-xs font-medium transition-colors flex items-center gap-1"
              style={{
                background: agentMode === m ? "var(--accent)" : "transparent",
                color: agentMode === m ? "#fff" : "var(--text-secondary)",
              }}
            >
              {m === "local" && <Monitor className="w-3 h-3" />}
              {AGENT_MODE_META[m].label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-2">
        {agentMode === "local" && (
          <button
            onClick={() => setShowLocalPanel(!showLocalPanel)}
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border transition-colors hover:opacity-80"
            style={{
              borderColor: localClients.length > 0 ? "#22c55e" : "var(--border-color)",
              color: localClients.length > 0 ? "#22c55e" : "var(--text-secondary)",
              background: localClients.length > 0 ? "rgba(34,197,94,0.1)" : "transparent",
            }}
          >
            {localClients.length > 0 ? <Wifi className="w-3.5 h-3.5" /> : <WifiOff className="w-3.5 h-3.5" />}
            {localClients.length > 0 ? `${localClients.length} 个客户端` : "无客户端"}
          </button>
        )}
        {messages.length > 0 && !streaming && (
          <>
            <button
              onClick={handleRegenerate}
              className="flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg border transition-colors hover:opacity-80"
              style={{ borderColor: "var(--border-color)", color: "var(--text-secondary)" }}
              title="重新生成上一条回复"
            >
              <RefreshCw className="w-3.5 h-3.5" /> 重试
            </button>
            <button
              onClick={handleExport}
              className="flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg border transition-colors hover:opacity-80"
              style={{ borderColor: "var(--border-color)", color: "var(--text-secondary)" }}
              title="导出为 Markdown"
            >
              <Download className="w-3.5 h-3.5" /> 导出
            </button>
          </>
        )}
        {streaming && (
          <button
            onClick={handleStop}
            className="flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg border transition-colors hover:opacity-80"
            style={{ borderColor: "var(--border-color)", color: "var(--text-secondary)" }}
          >
            <StopCircle className="w-3.5 h-3.5" /> 停止
          </button>
        )}
        {models.length > 0 && (
          <select
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
            className="text-xs px-3 py-1.5 rounded-lg border outline-none"
            style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
          >
            {models.map((m) => (
              <option key={m.name} value={m.name}>{m.display_name}</option>
            ))}
          </select>
        )}
        <button
          onClick={() => setShowMonitorPanel((value: boolean) => !value)}
          className="flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg border transition-colors hover:opacity-80"
          style={{
            borderColor: showMonitorPanel ? "var(--accent)" : "var(--border-color)",
            color: showMonitorPanel ? "var(--accent)" : "var(--text-secondary)",
            background: showMonitorPanel ? "rgba(99,102,241,0.1)" : "transparent",
          }}
          title="在当前页面查看 AI 后台监控"
        >
          <Activity className="w-3.5 h-3.5" /> 后台
        </button>
        <button
          onClick={toggleSpeculation}
          className="flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg border transition-colors hover:opacity-80"
          style={{
            borderColor: speculationEnabled ? "var(--accent)" : "var(--border-color)",
            color: speculationEnabled ? "var(--accent)" : "var(--text-secondary)",
            background: speculationEnabled ? "rgba(99,102,241,0.1)" : "transparent",
          }}
          title={speculationEnabled ? "点击关闭推测分支" : "点击开启推测分支"}
        >
          <Sparkles className="w-3.5 h-3.5" />
          {speculationEnabled ? "推测开" : "推测关"}
        </button>
      </div>
    </header>
  );
}
