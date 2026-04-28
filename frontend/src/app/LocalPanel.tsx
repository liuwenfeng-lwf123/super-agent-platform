"use client";

import { Monitor, Wifi, WifiOff, Shield, RefreshCw } from "lucide-react";
import { LOCAL_PERMISSION_OPTIONS } from "./chat-constants";
import type { LocalClient } from "@/types";
import type { LocalSchedule } from "@/lib/api";
import {
  setLocalToolPermission,
  createLocalShortcut,
  deleteLocalShortcut,
  createLocalSchedule,
  deleteLocalSchedule,
} from "@/lib/api";
import { useState } from "react";

interface LocalPanelProps {
  localClients: LocalClient[];
  localAuditLog: { timestamp: number; action: string; params_summary: Record<string, string>; success: boolean }[];
  toolStats: { tool: string; total: number; success: number }[];
  localShortcuts: { name: string; description: string; steps: string[] }[];
  localSchedules: LocalSchedule[];
  localPanelMessage: string;
  activeThreadId: string | null;
  setShowLocalPanel: (v: boolean) => void;
  handleBindLocalThread: (clientId: string) => void;
  handleSetLocalAutoApprove: (clientId: string, enabled: boolean) => void;
  handleSetLocalToolPermission: (clientId: string, tool: string, enabled: boolean) => void;
  loadLocalClients: () => void;
  loadLocalAuditLog: () => void;
  loadToolStats: () => void;
  loadShortcuts: () => void;
  loadSchedules: () => void;
  setInput: (v: string) => void;
}

export function LocalPanel({
  localClients,
  localAuditLog,
  toolStats,
  localShortcuts,
  localSchedules,
  localPanelMessage,
  activeThreadId,
  setShowLocalPanel,
  handleBindLocalThread,
  handleSetLocalAutoApprove,
  handleSetLocalToolPermission,
  loadLocalClients,
  loadLocalAuditLog,
  loadToolStats,
  loadShortcuts,
  loadSchedules,
  setInput,
}: LocalPanelProps) {
  const [showShortcutForm, setShowShortcutForm] = useState(false);
  const [shortcutFormName, setShortcutFormName] = useState("");
  const [shortcutFormSteps, setShortcutFormSteps] = useState("");
  const [showScheduleForm, setShowScheduleForm] = useState(false);
  const [scheduleFormMsg, setScheduleFormMsg] = useState("");
  const [scheduleFormInterval, setScheduleFormInterval] = useState("");

  return (
    <div className="w-80 flex-shrink-0 flex flex-col border-l" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
      <div className="p-3 border-b flex items-center justify-between" style={{ borderColor: "var(--border-color)" }}>
        <div className="flex items-center gap-2">
          <Monitor className="w-4 h-4" style={{ color: "var(--accent)" }} />
          <span className="text-sm font-semibold">本地模式</span>
        </div>
        <button onClick={() => setShowLocalPanel(false)} className="text-xs" style={{ color: "var(--text-secondary)" }}>关闭</button>
      </div>

      <div className="p-3 space-y-3 flex-1 overflow-y-auto">
        <div>
          <div className="text-xs font-medium mb-2" style={{ color: "var(--text-secondary)" }}>连接状态</div>
          {localClients.length === 0 ? (
            <div className="p-3 rounded-lg text-xs space-y-2" style={{ background: "var(--bg-primary)", border: "1px dashed var(--border-color)" }}>
              <div className="flex items-center gap-2" style={{ color: "var(--text-secondary)" }}>
                <WifiOff className="w-4 h-4" /> 当前没有连接的客户端
              </div>
              <p style={{ color: "var(--text-secondary)" }}>请在你的电脑上运行：</p>
              <code className="block p-2 rounded text-xs" style={{ background: "rgba(0,0,0,0.3)", color: "#22c55e" }}>
                cd /Users/lwf/CascadeProjects/super-agent-platform && python local_client.py
              </code>
              <p style={{ color: "var(--text-secondary)" }}>运行后这个面板会自动显示客户端；如果没有出现，请确认后端地址是 http://localhost:8001。</p>
            </div>
          ) : (
            localClients.map((c) => (
              <div key={c.client_id} className="p-3 rounded-lg text-xs space-y-1.5" style={{ background: "var(--bg-primary)" }}>
                <div className="flex items-center gap-2">
                  <Wifi className="w-3 h-3" style={{ color: "#22c55e" }} />
                  <span className="font-medium" style={{ color: "var(--text-primary)" }}>{c.info.hostname || c.client_id}</span>
                </div>
                <div style={{ color: "var(--text-secondary)" }}>
                  {c.info.os} {c.info.arch} | Python {c.info.python}
                </div>
                <button
                  onClick={() => handleBindLocalThread(c.client_id)}
                  className="mt-1 px-2 py-1 rounded text-xs font-medium"
                  style={{ background: "var(--accent)", color: "#fff" }}
                >
                  {activeThreadId ? "绑定到当前线程" : "新建会话并绑定"}
                </button>
                <div className="flex items-center gap-2 mt-1">
                  <Shield className="w-3 h-3" style={{ color: "var(--text-secondary)" }} />
                  <span style={{ color: "var(--text-secondary)" }}>自动批准：</span>
                  <button
                    onClick={() => handleSetLocalAutoApprove(c.client_id, !c.auto_approve)}
                    className="px-2 py-0.5 rounded text-xs font-medium"
                    style={{
                      background: c.auto_approve ? "#ef4444" : "var(--bg-secondary)",
                      color: c.auto_approve ? "#fff" : "var(--text-secondary)",
                      border: c.auto_approve ? "none" : "1px solid var(--border-color)",
                    }}
                  >
                    {c.auto_approve ? "开启" : "关闭"}
                  </button>
                </div>
                <div className="mt-2 pt-2 border-t" style={{ borderColor: "var(--border-color)" }}>
                  <div className="mb-1" style={{ color: "var(--text-secondary)" }}>多选自动允许：</div>
                  <div className="grid grid-cols-2 gap-1">
                    {LOCAL_PERMISSION_OPTIONS.map((option) => {
                      const enabled = c.auto_approve || (c.tool_auto_approve || []).includes(option.tool);
                      return (
                        <button
                          key={option.tool}
                          disabled={c.auto_approve}
                          title={option.desc}
                          onClick={() => handleSetLocalToolPermission(c.client_id, option.tool, !enabled)}
                          className="px-2 py-1 rounded text-left text-[11px] transition"
                          style={{
                            background: enabled ? "rgba(34,197,94,0.18)" : "var(--bg-secondary)",
                            color: enabled ? "#22c55e" : "var(--text-secondary)",
                            border: enabled ? "1px solid rgba(34,197,94,0.35)" : "1px solid var(--border-color)",
                            opacity: c.auto_approve ? 0.55 : 1,
                          }}
                        >
                          {enabled ? "✓ " : ""}{option.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
            ))
          )}
          {localPanelMessage && (
            <div className="mt-2 p-2 rounded text-xs" style={{ background: "var(--bg-primary)", color: localPanelMessage.includes("失败") ? "#ef4444" : "#22c55e" }}>
              {localPanelMessage}
            </div>
          )}
        </div>

        <div>
          <div className="text-xs font-medium mb-2" style={{ color: "var(--text-secondary)" }}>授权设置</div>
          <div className="text-xs space-y-1.5" style={{ color: "var(--text-secondary)" }}>
            <p>• <strong>全局自动批准</strong>：所有操作都不弹框</p>
            <p>• <strong>多选自动允许</strong>：只让选中的操作不弹框</p>
            <p>• <strong>未选中的操作</strong>：每次都会弹框确认</p>
          </div>
        </div>

        {/* Smart auto-approve suggestions */}
        {localClients.length > 0 && (() => {
          const client = localClients[0];
          const autoApproved = new Set(client.tool_auto_approve || []);
          const suggestions = toolStats
            .filter((s) => s.total >= 3 && !autoApproved.has(s.tool) && !client.auto_approve)
            .sort((a, b) => b.total - a.total)
            .slice(0, 3);
          if (suggestions.length === 0) return null;
          const labelMap = Object.fromEntries(LOCAL_PERMISSION_OPTIONS.map((o) => [o.tool, o.label]));
          return (
            <div>
              <div className="text-xs font-medium mb-2" style={{ color: "#f59e0b" }}>💡 建议自动批准</div>
              <div className="space-y-1">
                {suggestions.map((s) => (
                  <div key={s.tool} className="flex items-center justify-between p-2 rounded-lg text-[11px]"
                    style={{ background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.2)" }}>
                    <span style={{ color: "var(--text-primary)" }}>
                      {labelMap[s.tool] || s.tool} <span style={{ color: "var(--text-secondary)" }}>({s.total}次)</span>
                    </span>
                    <button
                      onClick={async () => {
                        await setLocalToolPermission(client.client_id, s.tool, true);
                        await loadLocalClients();
                        await loadToolStats();
                      }}
                      className="px-2 py-0.5 rounded text-[10px] font-medium"
                      style={{ background: "#f59e0b", color: "#fff" }}
                    >
                      开启
                    </button>
                  </div>
                ))}
              </div>
            </div>
          );
        })()}

        <div className="flex-1 min-h-0 flex flex-col">
          <div className="text-xs font-medium mb-2 flex items-center justify-between" style={{ color: "var(--text-secondary)" }}>
            <span>活动日志</span>
            <button onClick={loadLocalAuditLog} className="p-0.5 rounded hover:bg-black/20"><RefreshCw className="w-3 h-3" /></button>
          </div>
          <div className="text-[11px] mb-2 space-y-1" style={{ color: "var(--text-secondary)" }}>
            <p><strong>含义</strong>：记录 AI 通过本地客户端实际执行过的操作，方便你核对电脑被做了什么。</p>
            <p><strong>格式</strong>：时间 / ✓成功或✗失败 / 操作名 / 关键参数，例如应用名、路径或网址。</p>
            <p><strong>注意</strong>：弹框被拒绝或执行失败会显示 ✗；只授权但还没执行，不会生成活动记录。</p>
          </div>
          <div className="flex-1 min-h-[120px] max-h-[300px] overflow-y-auto rounded-lg p-2 font-mono text-[11px] leading-relaxed space-y-0.5" style={{ background: "rgba(0,0,0,0.4)", color: "#a1a1aa" }}>
            {localAuditLog.length === 0 ? (
              <div style={{ color: "#555" }}>暂无活动记录</div>
            ) : (
              [...localAuditLog].reverse().map((entry, i) => {
                const t = new Date(entry.timestamp * 1000);
                const time = `${t.getHours().toString().padStart(2, "0")}:${t.getMinutes().toString().padStart(2, "0")}:${t.getSeconds().toString().padStart(2, "0")}`;
                const params = Object.values(entry.params_summary || {}).join(", ").slice(0, 40);
                return (
                  <div key={i}>
                    <span style={{ color: "#555" }}>{time}</span>{" "}
                    <span style={{ color: entry.success ? "#22c55e" : "#ef4444" }}>{entry.success ? "✓" : "✗"}</span>{" "}
                    <span style={{ color: "#60a5fa" }}>{entry.action}</span>
                    {params && <span style={{ color: "#777" }}> {params}</span>}
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Shortcuts */}
        <div>
          <div className="text-xs font-medium mb-2 flex items-center justify-between" style={{ color: "var(--text-secondary)" }}>
            <span>快捷指令</span>
            <button onClick={() => setShowShortcutForm(!showShortcutForm)} className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--accent)", color: "#fff" }}>
              {showShortcutForm ? "取消" : "+ 新建"}
            </button>
          </div>
          {showShortcutForm && (
            <div className="mb-2 space-y-1.5 p-2 rounded-lg text-[11px]" style={{ background: "var(--bg-primary)", border: "1px solid var(--border-color)" }}>
              <input
                value={shortcutFormName}
                onChange={(e) => setShortcutFormName(e.target.value)}
                placeholder="指令名称（如：日报）"
                className="w-full px-2 py-1 rounded text-xs"
                style={{ background: "var(--bg-secondary)", color: "var(--text-primary)", border: "1px solid var(--border-color)" }}
              />
              <textarea
                value={shortcutFormSteps}
                onChange={(e) => setShortcutFormSteps(e.target.value)}
                placeholder={"每行一个步骤，例如：\n读取桌面的 notes.txt\n总结内容\n复制到剪贴板"}
                rows={3}
                className="w-full px-2 py-1 rounded text-xs resize-none"
                style={{ background: "var(--bg-secondary)", color: "var(--text-primary)", border: "1px solid var(--border-color)" }}
              />
              <button
                disabled={!shortcutFormName.trim() || !shortcutFormSteps.trim()}
                onClick={async () => {
                  const steps = shortcutFormSteps.split("\n").map((s) => s.trim()).filter(Boolean);
                  await createLocalShortcut(shortcutFormName.trim(), "", steps);
                  setShortcutFormName(""); setShortcutFormSteps(""); setShowShortcutForm(false);
                  await loadShortcuts();
                }}
                className="w-full py-1 rounded text-xs font-medium disabled:opacity-40"
                style={{ background: "var(--accent)", color: "#fff" }}
              >
                保存
              </button>
            </div>
          )}
          <div className="space-y-1">
            {localShortcuts.length === 0 ? (
              <div className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
                暂无快捷指令。点击「+ 新建」创建，或在对话中直接输入指令名称触发。
              </div>
            ) : (
              localShortcuts.map((sc) => (
                <div key={sc.name} className="flex items-start justify-between p-2 rounded-lg text-[11px]" style={{ background: "var(--bg-primary)" }}>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <button
                        onClick={() => setInput(sc.name)}
                        className="font-medium truncate hover:underline cursor-pointer"
                        style={{ color: "var(--accent)" }}
                      >
                        {sc.name}
                      </button>
                      <span style={{ color: "var(--text-secondary)" }}>({sc.steps.length}步)</span>
                    </div>
                    <div className="truncate mt-0.5" style={{ color: "var(--text-secondary)" }}>{sc.steps[0]}</div>
                  </div>
                  <button
                    onClick={async () => { await deleteLocalShortcut(sc.name); await loadShortcuts(); }}
                    className="text-[10px] ml-1 px-1 opacity-40 hover:opacity-100"
                    style={{ color: "#ef4444" }}
                  >
                    ✕
                  </button>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Schedules */}
        <div>
          <div className="text-xs font-medium mb-2 flex items-center justify-between" style={{ color: "var(--text-secondary)" }}>
            <span>定时任务</span>
            <button onClick={() => setShowScheduleForm(!showScheduleForm)} className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--accent)", color: "#fff" }}>
              {showScheduleForm ? "取消" : "+ 新建"}
            </button>
          </div>
          {showScheduleForm && (
            <div className="mb-2 space-y-1.5 p-2 rounded-lg text-[11px]" style={{ background: "var(--bg-primary)", border: "1px solid var(--border-color)" }}>
              <input
                value={scheduleFormMsg}
                onChange={(e) => setScheduleFormMsg(e.target.value)}
                placeholder="提醒内容（如：该喝水了）"
                className="w-full px-2 py-1 rounded text-xs"
                style={{ background: "var(--bg-secondary)", color: "var(--text-primary)", border: "1px solid var(--border-color)" }}
              />
              <input
                value={scheduleFormInterval}
                onChange={(e) => setScheduleFormInterval(e.target.value)}
                placeholder="间隔分钟数（如：30）"
                type="number"
                min={1}
                className="w-full px-2 py-1 rounded text-xs"
                style={{ background: "var(--bg-secondary)", color: "var(--text-primary)", border: "1px solid var(--border-color)" }}
              />
              <button
                disabled={!scheduleFormMsg.trim() || !scheduleFormInterval.trim()}
                onClick={async () => {
                  await createLocalSchedule({
                    message: scheduleFormMsg.trim(),
                    interval_minutes: parseInt(scheduleFormInterval) || 30,
                  });
                  setScheduleFormMsg(""); setScheduleFormInterval(""); setShowScheduleForm(false);
                  await loadSchedules();
                }}
                className="w-full py-1 rounded text-xs font-medium disabled:opacity-40"
                style={{ background: "var(--accent)", color: "#fff" }}
              >
                创建
              </button>
            </div>
          )}
          <div className="space-y-1">
            {localSchedules.length === 0 ? (
              <div className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
                暂无定时任务。也可以在对话里说「每30分钟提醒我喝水」自动创建。
              </div>
            ) : (
              localSchedules.map((sch) => (
                <div key={sch.id} className="flex items-start justify-between p-2 rounded-lg text-[11px]" style={{ background: "var(--bg-primary)" }}>
                  <div className="flex-1 min-w-0">
                    <div className="font-medium truncate" style={{ color: sch.enabled ? "var(--text-primary)" : "var(--text-secondary)" }}>
                      {sch.message}
                    </div>
                    <div className="mt-0.5" style={{ color: "var(--text-secondary)" }}>
                      {sch.interval_minutes ? `每 ${sch.interval_minutes} 分钟` : sch.run_at ? `一次性：${new Date(sch.run_at).toLocaleString("zh-CN")}` : "未设定"}
                      {sch.last_run && ` · 上次：${new Date(sch.last_run).toLocaleTimeString("zh-CN")}`}
                    </div>
                  </div>
                  <button
                    onClick={async () => { await deleteLocalSchedule(sch.id); await loadSchedules(); }}
                    className="text-[10px] ml-1 px-1 opacity-40 hover:opacity-100"
                    style={{ color: "#ef4444" }}
                  >
                    ✕
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
