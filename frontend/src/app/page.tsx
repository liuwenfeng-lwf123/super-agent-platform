"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import {
  Send,
  Plus,
  Settings,
  MessageSquare,
  Trash2,
  Moon,
  Sun,
  Zap,
  Bot,
  User,
  Loader2,
  Paperclip,
  Code2,
  StopCircle,
} from "lucide-react";
import {
  sendMessage,
  fetchThreads,
  deleteThread,
  fetchSkills,
  fetchModels,
} from "@/lib/api";
import type { ThreadListItem, SSEEventData, SkillConfig, ModelConfig } from "@/types";
import type { ToolCallInfo } from "@/components/MessageRenderer";
import { MessageContent, ToolCallsPanel, WebPreviewPanel } from "@/components/MessageRenderer";
import { FileAttachment, parseFileAttachments } from "@/components/FileAttachment";
import { WorkspacePanel } from "@/components/WorkspacePanel";
import dynamic from "next/dynamic";

const SettingsPage = dynamic(() => import("@/components/SettingsPanel"), { ssr: false });

export default function ChatPage() {
  const [threads, setThreads] = useState<ThreadListItem[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamContent, setStreamContent] = useState("");
  const [darkMode, setDarkMode] = useState(true);
  const [skills, setSkills] = useState<SkillConfig[]>([]);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [showSettingsPage, setShowSettingsPage] = useState(false);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [previewHtml, setPreviewHtml] = useState<string | null>(null);
  const [agentMode, setAgentMode] = useState<"flash" | "standard" | "pro" | "ultra">("standard");
  const [agentStatuses, setAgentStatuses] = useState<{ id: string; status: string; task?: string }[]>([]);
  const [toolCalls, setToolCalls] = useState<ToolCallInfo[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    loadThreads();
    loadSkills();
    loadModels();
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamContent, toolCalls]);

  const loadThreads = async () => {
    try {
      const data = await fetchThreads();
      setThreads(data);
    } catch {}
  };

  const loadSkills = async () => {
    try {
      const data = await fetchSkills();
      setSkills(data);
    } catch {}
  };

  const loadModels = async () => {
    try {
      const data = await fetchModels();
      setModels(data);
      if (data.length > 0) setSelectedModel(data[0].name);
    } catch {}
  };

  const handleNewChat = () => {
    setActiveThreadId(null);
    setMessages([]);
    setStreamContent("");
    setPreviewHtml(null);
    inputRef.current?.focus();
  };

  const handleSelectThread = async (id: string) => {
    setActiveThreadId(id);
    setStreamContent("");
    setPreviewHtml(null);
    try {
      const res = await fetch(`/api/threads/${id}`);
      const thread = await res.json();
      setMessages(thread.messages || []);
    } catch {}
  };

  const handleDeleteThread = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    await deleteThread(id);
    if (activeThreadId === id) {
      setActiveThreadId(null);
      setMessages([]);
    }
    loadThreads();
  };

  const handleStop = () => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
  };

  const handleSend = useCallback(async () => {
    if (!input.trim() || streaming) return;

    const userMsg = { role: "user", content: input.trim() };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setStreaming(true);
    setStreamContent("");
    setToolCalls([]);
    setAgentStatuses([]);

    let collected = "";

    try {
      await sendMessage(
        {
          thread_id: activeThreadId,
          message: userMsg.content,
          model: selectedModel || undefined,
          mode: agentMode,
        },
        (event: SSEEventData) => {
          if (event.type === "token" && event.content) {
            collected += event.content;
            setStreamContent(collected);
          } else if (event.type === "done" && event.thread_id) {
            setActiveThreadId(event.thread_id);
            loadThreads();
          } else if (event.type === "plan" && event.data) {
            const steps = (event.data as { steps?: { id: string; task: string }[] }).steps || [];
            setAgentStatuses(steps.map((s) => ({ id: s.id, status: "pending", task: s.task })));
          } else if (event.type === "agent_status" && event.data) {
            const d = event.data as { agent_id: string; status: string; task?: string };
            setAgentStatuses((prev) => {
              const exists = prev.find((a) => a.id === d.agent_id);
              if (exists) return prev.map((a) => (a.id === d.agent_id ? { ...a, status: d.status } : a));
              return [...prev, { id: d.agent_id, status: d.status, task: d.task }];
            });
          } else if (event.type === "tool_call" && event.data) {
            const d = event.data as { tool: string; status: string; input?: string };
            setToolCalls((prev) => [...prev, { tool: d.tool, status: d.status, input: d.input }]);
          } else if (event.type === "tool_result" && event.data) {
            const d = event.data as { tool: string; status: string; output?: string };
            setToolCalls((prev) =>
              prev.map((tc) =>
                tc.tool === d.tool && tc.status === "running"
                  ? { ...tc, status: "completed", output: d.output }
                  : tc
              )
            );
          } else if (event.type === "error") {
            collected += `\n\n**Error:** ${event.content}`;
            setStreamContent(collected);
          }
        }
      );
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") {
        collected += "\n\n*[Stopped by user]*";
      } else {
        collected += "\n\n**Connection error. Please check if the backend is running.**";
      }
      setStreamContent(collected);
    }

    setMessages((prev) => [
      ...prev,
      { role: "assistant", content: collected || "No response" },
    ]);
    if (collected) extractHtmlPreview(collected);
    setStreamContent("");
    setStreaming(false);
    setAgentStatuses([]);
    setToolCalls([]);
  }, [input, streaming, activeThreadId, selectedModel, agentMode]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const toggleDark = () => {
    setDarkMode(!darkMode);
    document.documentElement.classList.toggle("dark");
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch("/api/upload", { method: "POST", body: formData });
      const data = await res.json();
      setInput((prev) => prev + `\n[Attached file: ${data.filename} (${data.size} bytes)]`);
    } catch {}
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const extractHtmlPreview = (content: string) => {
    const htmlMatch = content.match(/```html\s*\n([\s\S]*?)```/);
    if (htmlMatch) {
      setPreviewHtml(htmlMatch[1]);
    }
  };

  if (showSettingsPage) {
    return (
      <div className="h-screen" style={{ background: "var(--bg-primary)" }}>
        <SettingsPage onBack={() => setShowSettingsPage(false)} />
      </div>
    );
  }

  return (
    <div className="flex h-screen" style={{ background: "var(--bg-primary)" }}>
      {/* Sidebar */}
      <aside
        className="w-64 flex-shrink-0 flex flex-col border-r"
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

        <div className="flex-1 overflow-y-auto p-2 scrollbar-thin">
          {threads.map((t) => (
            <div
              key={t.id}
              onClick={() => handleSelectThread(t.id)}
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
              No conversations yet
            </p>
          )}
        </div>

        <div className="p-3 border-t flex items-center justify-between" style={{ borderColor: "var(--border-color)" }}>
          <button onClick={toggleDark} className="p-2 rounded-lg hover:opacity-80" style={{ color: "var(--text-secondary)" }}>
            {darkMode ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
          </button>
          <button onClick={() => setShowSettingsPage(true)} className="p-2 rounded-lg hover:opacity-80" style={{ color: "var(--text-secondary)" }}>
            <Settings className="w-4 h-4" />
          </button>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="h-14 flex items-center justify-between px-6 border-b" style={{ borderColor: "var(--border-color)" }}>
          <div className="flex items-center gap-3">
            <h1 className="text-sm font-semibold">
              {activeThreadId ? "Conversation" : "New Chat"}
            </h1>
            <div className="flex rounded-lg overflow-hidden border" style={{ borderColor: "var(--border-color)" }}>
              {(["flash", "standard", "pro", "ultra"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setAgentMode(m)}
                  className="px-2 py-1 text-xs font-medium transition-colors"
                  style={{
                    background: agentMode === m ? "var(--accent)" : "transparent",
                    color: agentMode === m ? "#fff" : "var(--text-secondary)",
                  }}
                >
                  {m.charAt(0).toUpperCase() + m.slice(1)}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {streaming && (
              <button
                onClick={handleStop}
                className="flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg border transition-colors hover:opacity-80"
                style={{ borderColor: "var(--border-color)", color: "var(--text-secondary)" }}
              >
                <StopCircle className="w-3.5 h-3.5" /> Stop
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
          </div>
        </header>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-6 py-4 scrollbar-thin">
          {messages.length === 0 && !streaming && (
            <div className="flex flex-col items-center justify-center h-full gap-4">
              <div className="w-16 h-16 rounded-2xl flex items-center justify-center" style={{ background: "var(--accent-light)" }}>
                <Bot className="w-8 h-8" style={{ color: "var(--accent)" }} />
              </div>
              <h2 className="text-xl font-semibold">天工流</h2>
              <p className="text-sm max-w-md text-center" style={{ color: "var(--text-secondary)" }}>
                AI Super Agent — 搜索、编码、创作，一气呵成
              </p>

              <div className="flex gap-3 mt-2">
                {([
                  { mode: "flash", desc: "Fast chat, no tools", icon: "Z" },
                  { mode: "standard", desc: "Tools + workspace", icon: "S" },
                  { mode: "pro", desc: "Plan then execute", icon: "P" },
                  { mode: "ultra", desc: "Multi-agent parallel", icon: "U" },
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
                    <span className="text-xs font-medium">{m.mode}</span>
                    <span className="text-[10px]" style={{ color: "var(--text-secondary)" }}>{m.desc}</span>
                  </button>
                ))}
              </div>

              <div className="flex flex-wrap gap-2 mt-3 max-w-lg justify-center">
                {skills.map((s) => (
                  <span
                    key={s.name}
                    className="px-3 py-1.5 rounded-full text-xs font-medium"
                    style={{ background: "var(--accent-light)", color: "var(--accent)" }}
                  >
                    {s.display_name}
                  </span>
                ))}
              </div>
              <div className="flex flex-wrap gap-2 mt-4 max-w-lg justify-center">
                {[
                  { label: "Create a web app", icon: "G" },
                  { label: "Analyze data with Python", icon: "D" },
                  { label: "Write a research report", icon: "R" },
                  { label: "Build presentation slides", icon: "P" },
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
              <div key={i} className="flex gap-3 mb-5 max-w-3xl mx-auto">
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
                  <div className="text-xs font-medium mb-1" style={{ color: "var(--text-secondary)" }}>
                    {isUser ? "You" : "Super Agent"}
                  </div>
                  {attachments.length > 0 && (
                    <div className="flex flex-wrap gap-2 mb-2">
                      {attachments.map((a, j) => (
                        <FileAttachment key={j} filename={a.filename} size={a.size} />
                      ))}
                    </div>
                  )}
                  <MessageContent content={text} isUser={isUser} />
                </div>
              </div>
            );
          })}

          {streaming && toolCalls.length > 0 && (
            <ToolCallsPanel toolCalls={toolCalls} />
          )}

          {agentStatuses.length > 0 && (
            <div className="max-w-3xl mx-auto mb-4 p-3 rounded-xl border" style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}>
              <div className="text-xs font-medium mb-2" style={{ color: "var(--accent)" }}>
                <Code2 className="w-3.5 h-3.5 inline mr-1" />Agent Status
              </div>
              <div className="space-y-1.5">
                {agentStatuses.map((a) => (
                  <div key={a.id} className="flex items-center gap-2 text-xs">
                    <span
                      className={`w-2 h-2 rounded-full ${
                        a.status === "completed"
                          ? "bg-green-400"
                          : a.status === "running"
                          ? "bg-yellow-400 animate-pulse"
                          : a.status === "failed"
                          ? "bg-red-400"
                          : "bg-gray-400"
                      }`}
                    />
                    <span style={{ color: "var(--text-primary)" }}>{a.id}</span>
                    <span style={{ color: "var(--text-secondary)" }}>{a.task || a.status}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {streaming && streamContent && (
            <div className="flex gap-3 mb-5 max-w-3xl mx-auto">
              <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 mt-1" style={{ background: "var(--assistant-bubble)" }}>
                <Bot className="w-3.5 h-3.5" style={{ color: "var(--accent)" }} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-xs font-medium mb-1" style={{ color: "var(--text-secondary)" }}>Super Agent</div>
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

        {/* Web Preview Panel */}
        {previewHtml && (
          <WebPreviewPanel html={previewHtml} onClose={() => setPreviewHtml(null)} />
        )}

        {/* Input */}
        <div className="px-6 pb-5">
          <div className="max-w-3xl mx-auto relative flex items-end gap-2">
            <input ref={fileInputRef} type="file" className="hidden" onChange={handleFileUpload} />
            <button
              onClick={() => fileInputRef.current?.click()}
              className="p-2.5 rounded-xl border flex-shrink-0"
              style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-secondary)" }}
              title="Upload file"
            >
              <Paperclip className="w-4 h-4" />
            </button>
            <div className="flex-1 relative">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Send a message... Try 'search AI news' or 'run python code'"
                rows={1}
                className="w-full pl-4 pr-12 py-3 rounded-xl border text-sm resize-none outline-none focus:ring-2 transition-shadow"
                style={{
                  background: "var(--bg-secondary)",
                  borderColor: "var(--border-color)",
                  color: "var(--text-primary)",
                  minHeight: "48px",
                  maxHeight: "160px",
                }}
                onInput={(e) => {
                  const el = e.target as HTMLTextAreaElement;
                  el.style.height = "48px";
                  el.style.height = Math.min(el.scrollHeight, 160) + "px";
                }}
              />
              <button
                onClick={streaming ? handleStop : handleSend}
                disabled={!streaming && !input.trim()}
                className="absolute right-3 top-1/2 -translate-y-1/2 p-1.5 rounded-lg disabled:opacity-30 transition-opacity"
                style={{ background: "var(--accent)", color: "#fff" }}
              >
                {streaming ? <StopCircle className="w-4 h-4" /> : <Send className="w-4 h-4" />}
              </button>
            </div>
          </div>
        </div>
      </main>

      {/* Workspace Panel */}
      <WorkspacePanel threadId={activeThreadId} onPreviewHtml={(html) => setPreviewHtml(html)} />
    </div>
  );
}
