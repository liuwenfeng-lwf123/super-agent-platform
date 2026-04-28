"use client";

import { useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { fetchThreads } from "@/lib/api";
import { ToolMonitorPanel } from "@/components/ToolMonitorPanel";
import type { ThreadListItem } from "@/types";

export default function MonitorPage() {
  const [threads, setThreads] = useState<ThreadListItem[]>([]);

  useEffect(() => {
    fetchThreads().then((data) => setThreads(Array.isArray(data) ? data : [])).catch(() => setThreads([]));
  }, []);

  return (
    <div className="h-screen flex flex-col" style={{ background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      <div className="flex items-center gap-3 px-6 py-4 border-b" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
        <button onClick={() => window.location.href = "/"} className="p-2 rounded-lg hover:bg-white/5">
          <ArrowLeft size={18} />
        </button>
        <div>
          <h1 className="text-lg font-semibold">AI 后台监控</h1>
          <div className="text-xs" style={{ color: "var(--text-secondary)" }}>如果你更喜欢不跳页，也可以在聊天页点“后台”右侧打开。</div>
        </div>
      </div>
      <div className="flex-1 overflow-hidden">
        <ToolMonitorPanel threads={threads} />
      </div>
    </div>
  );
}
