"use client";

import {
  Plus,
  Settings,
  MessageSquare,
  Trash2,
  Moon,
  Sun,
  Zap,
  Search,
  Brain,
  BarChart3,
  GitBranch,
  Dna,
  Atom,
} from "lucide-react";
import type { ThreadListItem } from "@/types";

interface ChatSidebarProps {
  threads: ThreadListItem[];
  activeThreadId: string | null;
  threadSearch: string;
  setThreadSearch: (v: string) => void;
  sidebarOpen: boolean;
  setSidebarOpen: (v: boolean) => void;
  darkMode: boolean;
  toggleDark: () => void;
  handleNewChat: () => void;
  handleSelectThread: (id: string) => void;
  handleDeleteThread: (id: string, e: React.MouseEvent) => void;
  loadThreads: () => void;
  setShowSettingsPage: (v: boolean) => void;
}

export function ChatSidebar({
  threads,
  activeThreadId,
  threadSearch,
  setThreadSearch,
  sidebarOpen,
  setSidebarOpen,
  darkMode,
  toggleDark,
  handleNewChat,
  handleSelectThread,
  handleDeleteThread,
  loadThreads,
  setShowSettingsPage,
}: ChatSidebarProps) {
  return (
    <aside
      className={`w-64 flex-shrink-0 flex flex-col border-r fixed md:relative inset-y-0 left-0 z-40 transition-transform duration-200 ${sidebarOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"}`}
      style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}
    >
      <div className="p-4 flex items-center justify-between border-b" style={{ borderColor: "var(--border-color)" }}>
        <div className="flex items-center gap-2">
          <Zap className="w-5 h-5" style={{ color: "var(--accent)" }} />
          <span className="font-bold text-sm">天工流</span>
        </div>
        <button onClick={handleNewChat} className="p-1.5 rounded-lg hover:opacity-80" style={{ background: "var(--accent)", color: "#fff" }}>
          <Plus className="w-4 h-4" />
        </button>
      </div>

      <div className="px-2 pt-2 pb-1">
        <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: "var(--text-secondary)" }} />
          <input
            type="text"
            placeholder="搜索对话..."
            value={threadSearch}
            onChange={(e) => { setThreadSearch(e.target.value); if (e.target.value.length === 1) loadThreads(); }}
            onFocus={() => loadThreads()}
            className="w-full pl-8 pr-3 py-1.5 text-xs rounded-lg border outline-none"
            style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2 scrollbar-thin">
        {threads.filter((t) => {
          if (!threadSearch) return true;
          const q = threadSearch.toLowerCase();
          return t.title.toLowerCase().includes(q) || !!(t.last_message && t.last_message.toLowerCase().includes(q));
        }).map((t) => (
          <div
            key={t.id}
            onClick={() => { handleSelectThread(t.id); setSidebarOpen(false); }}
            className="group flex items-center gap-2 px-3 py-2.5 rounded-lg cursor-pointer mb-1 text-sm transition-colors"
            style={{
              background: activeThreadId === t.id ? "var(--accent-light)" : "transparent",
              color: activeThreadId === t.id ? "var(--accent)" : "var(--text-secondary)",
            }}
          >
            <MessageSquare className="w-4 h-4 flex-shrink-0" />
            <span className="truncate flex-1">{t.title}</span>
            <button
              onClick={(e) => handleDeleteThread(t.id, e)}
              className="opacity-0 group-hover:opacity-100 p-0.5 hover:text-red-400 transition-opacity"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
        {threads.length === 0 && (
          <p className="text-center text-xs py-8" style={{ color: "var(--text-secondary)" }}>
            还没有对话
          </p>
        )}
      </div>

      <div className="p-3 border-t flex items-center justify-between" style={{ borderColor: "var(--border-color)" }}>
        <div className="flex items-center gap-1">
          <button onClick={toggleDark} className="p-2 rounded-lg hover:opacity-80" style={{ color: "var(--text-secondary)" }} title="切换主题">
            {darkMode ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
          </button>
          <button onClick={() => window.location.href = "/memory"} className="p-2 rounded-lg hover:opacity-80" style={{ color: "var(--text-secondary)" }} title="记忆管理">
            <Brain className="w-4 h-4" />
          </button>
          <button onClick={() => window.location.href = "/dashboard"} className="p-2 rounded-lg hover:opacity-80" style={{ color: "var(--text-secondary)" }} title="成本仪表盘">
            <BarChart3 className="w-4 h-4" />
          </button>
          <button onClick={() => window.location.href = "/history"} className="p-2 rounded-lg hover:opacity-80" style={{ color: "var(--text-secondary)" }} title="文件历史">
            <GitBranch className="w-4 h-4" />
          </button>
          <button onClick={() => window.location.href = "/evolution"} className="p-2 rounded-lg hover:opacity-80" style={{ color: "var(--text-secondary)" }} title="自进化">
            <Dna className="w-4 h-4" />
          </button>
          <button onClick={() => window.location.href = "/hermes"} className="p-2 rounded-lg hover:opacity-80" style={{ color: "var(--text-secondary)" }} title="Hermes 控制台 (子Agent/钩子/定时/插件/GEPA)">
            <Atom className="w-4 h-4" />
          </button>
        </div>
        <button onClick={() => setShowSettingsPage(true)} className="p-2 rounded-lg hover:opacity-80" style={{ color: "var(--text-secondary)" }} title="设置">
          <Settings className="w-4 h-4" />
        </button>
      </div>
    </aside>
  );
}
