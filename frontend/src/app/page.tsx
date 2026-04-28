"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import {
  Activity,
} from "lucide-react";
import {
  sendMessage,
  fetchThreads,
  fetchThread,
  deleteThread,
  fetchSkills,
  fetchModels,
  sendLocalMessage,
  fetchCustomTools,
  approvePermissionRequest,
  denyPermissionRequest,
  fetchPendingPermissions,
  setLocalToolPermission,
} from "@/lib/api";
import { isSlashCommand, dispatchSlashCommand, getSlashCompletions, type SlashCommand } from "@/lib/slash";
import type {
  ThreadListItem,
  SSEEventData,
  SkillConfig,
  ModelConfig,
  PromptSuggestionEventData,
  SpeculationNotice,
  SpeculationRecord,
} from "@/types";
import type { ToolCallInfo } from "@/components/MessageRenderer";
import { WebPreviewPanel } from "@/components/MessageRenderer";
import { WorkspacePanel } from "@/components/WorkspacePanel";
import { ToolMonitorPanel } from "@/components/ToolMonitorPanel";
import dynamic from "next/dynamic";
import {
  AGENT_MODE_META,
  TOOL_NAME_LABELS,
  pickPreferredModel,
  buildChatErrorMessage,
} from "./chat-constants";
import type { ChatTool, PermissionPrompt } from "./chat-constants";

const SettingsPage = dynamic(() => import("@/components/SettingsPanel"), { ssr: false });
import { ChatSidebar } from "./ChatSidebar";
import { LocalPanel } from "./LocalPanel";
import { ChatHeader } from "./ChatHeader";
import { ChatInput } from "./ChatInput";
import { MessageArea } from "./MessageArea";
import { useSpeculation } from "./useSpeculation";
import { useLocalMode } from "./useLocalMode";

export default function ChatPage() {
  const [threads, setThreads] = useState<ThreadListItem[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<{ role: string; content: string; _streaming_terminal?: boolean; usage?: { input_tokens?: number; output_tokens?: number; cost_usd?: number; tool_calls?: number; agents_spawned?: number } }[]>([]);
  const [input, setInput] = useState("");
  const [slashCompletions, setSlashCompletions] = useState<SlashCommand[]>([]);
  const [slashSelectedIdx, setSlashSelectedIdx] = useState(0);
  const [streaming, setStreaming] = useState(false);
  const [streamContent, setStreamContent] = useState("");
  const [darkMode, setDarkMode] = useState(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("darkMode");
      return saved !== null ? saved === "true" : true;
    }
    return true;
  });
  const [skills, setSkills] = useState<SkillConfig[]>([]);
  const [availableTools, setAvailableTools] = useState<ChatTool[]>([]);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [showSettingsPage, setShowSettingsPage] = useState(false);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [previewHtml, setPreviewHtml] = useState<string | null>(null);
  const [agentMode, setAgentMode] = useState<"flash" | "standard" | "pro" | "ultra" | "local">("standard");
  const [showMonitorPanel, setShowMonitorPanel] = useState(false);
  const [showSkillPanel, setShowSkillPanel] = useState(false);
  const [showToolPanel, setShowToolPanel] = useState(false);
  const [selectedSkillNames, setSelectedSkillNames] = useState<string[]>([]);
  const [selectedToolNames, setSelectedToolNames] = useState<string[]>([]);
  const [toolSearch, setToolSearch] = useState("");
  const [threadSearch, setThreadSearch] = useState("");
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editingContent, setEditingContent] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [pendingImages, setPendingImages] = useState<string[]>([]);
  const [agentStatuses, setAgentStatuses] = useState<{ id: string; status: string; task?: string; role?: string; tool_calls?: {tool: string}[]; result_preview?: string }[]>([]);
  const [toolCalls, setToolCalls] = useState<ToolCallInfo[]>([]);
  const [fileDiffs, setFileDiffs] = useState<import("@/components/DiffViewer").FileDiff[]>([]);
  const [pendingPermission, setPendingPermission] = useState<PermissionPrompt | null>(null);
  const [lastUsage, setLastUsage] = useState<{ input_tokens?: number; output_tokens?: number; cost_usd?: number; tool_calls?: number; agents_spawned?: number } | null>(null);
  const {
    speculationEnabled,
    speculationRecord,
    setSpeculationRecord,
    speculationDiffs,
    setSpeculationDiffs,
    speculationDiffLoading,
    speculationBusyAction,
    setSpeculationBusyAction,
    speculationNotice,
    setSpeculationNotice,
    workspaceRefreshToken,
    setWorkspaceRefreshToken,
    loadSpeculation,
    toggleSpeculation,
    handleRefreshSpeculation,
    handleAcceptSpeculation,
    handleDiscardSpeculation,
    resetSpeculation,
  } = useSpeculation(activeThreadId);

  const loadThreads = useCallback(async () => {
    try {
      const data = await fetchThreads();
      setThreads(data);
    } catch {}
  }, []);

  const {
    localClients,
    localAuditLog,
    toolStats,
    localShortcuts,
    localSchedules,
    showLocalPanel,
    setShowLocalPanel,
    localDisconnectNotice,
    setLocalDisconnectNotice,
    localPanelMessage,
    loadLocalClients,
    loadLocalAuditLog,
    loadToolStats,
    loadShortcuts,
    loadSchedules,
    handleBindLocalThread,
    handleSetLocalAutoApprove,
    handleSetLocalToolPermission,
  } = useLocalMode({ activeThreadId, setActiveThreadId, loadThreads });
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", darkMode);
    loadThreads();
    loadSkills();
    loadTools();
    loadModels();
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamContent, toolCalls]);

  useEffect(() => {
    // Listen for /clear slash command
    const handler = () => setMessages([]);
    window.addEventListener("slash:clear", handler);
    return () => window.removeEventListener("slash:clear", handler);
  }, []);

  const loadSkills = async () => {
    try {
      const data = await fetchSkills();
      setSkills(data);
    } catch {}
  };

  const loadTools = async () => {
    try {
      const data = await fetchCustomTools();
      setAvailableTools(Array.isArray(data.tools) ? data.tools : []);
    } catch {
      setAvailableTools([]);
    }
  };

  const loadModels = async () => {
    try {
      const data = await fetchModels();
      setModels(data);
      if (data.length > 0) {
        setSelectedModel((current) => {
          const currentModel = data.find((model: ModelConfig) => model.name === current);
          if (currentModel?.has_api_key) return current;
          return pickPreferredModel(data);
        });
      }
    } catch {}
  };

  const handleNewChat = () => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setStreaming(false);
    setActiveThreadId(null);
    setMessages([]);
    setStreamContent("");
    setToolCalls([]);
    setAgentStatuses([]);
    setPreviewHtml(null);
    resetSpeculation();
    inputRef.current?.focus();
  };

  const toggleSelectedSkill = (skillName: string) => {
    setSelectedSkillNames((prev) =>
      prev.includes(skillName)
        ? prev.filter((name) => name !== skillName)
        : [...prev, skillName]
    );
  };

  const toggleSelectedTool = (toolName: string) => {
    setSelectedToolNames((prev) =>
      prev.includes(toolName)
        ? prev.filter((name) => name !== toolName)
        : [...prev, toolName]
    );
  };

  const handleSelectThread = async (id: string) => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setStreaming(false);
    setActiveThreadId(id);
    setStreamContent("");
    setToolCalls([]);
    setAgentStatuses([]);
    setPreviewHtml(null);
    setSpeculationNotice(null);
    try {
      const thread = await fetchThread(id);
      setMessages(thread.messages || []);
    } catch {
      setMessages([]);
    }
  };

  const handleDeleteThread = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await deleteThread(id);
    } catch {
      return;
    }
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
    setStreaming(false);
  };

  const resolvePendingPermission = async (approve: boolean, alwaysAllow?: boolean) => {
    if (!pendingPermission) return;
    const requestId = pendingPermission.request_id;
    const toolName = pendingPermission.tool;
    try {
      if (approve) {
        if (alwaysAllow && toolName && localClients.length > 0) {
          await setLocalToolPermission(localClients[0].client_id, toolName, true);
          await loadLocalClients();
        }
        await approvePermissionRequest(requestId);
      } else {
        await denyPermissionRequest(requestId);
      }
      setPendingPermission((current) => current?.request_id === requestId ? null : current);
    } catch {
      setPendingPermission((current) => current?.request_id === requestId ? { ...current, reason: "权限处理失败，请重试" } : current);
    }
  };

  const doSend = useCallback(async (messageContent: string, images?: string[]) => {
    if (!messageContent.trim() || streaming) return;

    const myRequestId = ++requestIdRef.current;

    setStreaming(true);
    setStreamContent("");
    setToolCalls([]);
    setFileDiffs([]);
    setPendingPermission(null);
    setAgentStatuses([]);
    setSpeculationDiffs(null);
    setSpeculationBusyAction(null);
    setSpeculationNotice(null);

    let collected = "";
    let usageData: { input_tokens?: number; output_tokens?: number; cost_usd?: number; tool_calls?: number; agents_spawned?: number } | null = null;
    let latestThreadId = activeThreadId || null;
    let permissionPoll: ReturnType<typeof setInterval> | null = null;

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      if (agentMode === "local") {
        const requestStartedAt = Date.now() - 1000;
        permissionPoll = setInterval(async () => {
          try {
            const payload = await fetchPendingPermissions(latestThreadId || activeThreadId || undefined);
            const requests = Array.isArray(payload?.requests) ? payload.requests : [];
            const pending = requests
              .filter((item: PermissionPrompt & { created_at?: string }) => {
                if (item.status !== "pending") return false;
                const createdAt = item.created_at ? Date.parse(item.created_at) : Date.now();
                return Number.isNaN(createdAt) || createdAt >= requestStartedAt;
              })
              .sort((a: PermissionPrompt & { created_at?: string }, b: PermissionPrompt & { created_at?: string }) => {
                const left = a.created_at ? Date.parse(a.created_at) : 0;
                const right = b.created_at ? Date.parse(b.created_at) : 0;
                return right - left;
              })[0];
            if (pending) {
              latestThreadId = pending.thread_id || latestThreadId;
              setPendingPermission((current) => current?.request_id === pending.request_id ? current : pending);
            }
          } catch {
          }
        }, 1000);
      }

      const sendFn = agentMode === "local" ? sendLocalMessage : sendMessage;
      await sendFn(
        {
          thread_id: activeThreadId || undefined,
          message: messageContent,
          model: selectedModel || undefined,
          skills: selectedSkillNames.length > 0 ? selectedSkillNames : undefined,
          tools: selectedToolNames.length > 0 ? selectedToolNames : undefined,
          mode: agentMode === "local" ? "local" : agentMode,
          images: images && images.length > 0 ? images : undefined,
          enable_speculation: speculationEnabled ? undefined : false,
        },
        (event: SSEEventData) => {
          if (event.type === "token" && event.content) {
            collected += event.content;
            setStreamContent(collected);
          } else if (event.type === "done") {
            setStreaming(false);
            setPendingPermission(null);
            setAgentStatuses([]);
            if (event.thread_id) {
              latestThreadId = event.thread_id;
              setActiveThreadId(event.thread_id);
              loadThreads();
            }
            if (event.usage) {
              setLastUsage(event.usage);
              usageData = event.usage;
            }
          } else if (event.type === "plan" && event.data) {
            const steps = (event.data as { steps?: { id: string; task: string }[] }).steps || [];
            setAgentStatuses(steps.map((s) => ({ id: s.id, status: "pending", task: s.task })));
          } else if (event.type === "agent_status" && event.data) {
            const d = event.data as { agent_id: string; status: string; task?: string; role?: string; tool_calls?: {tool: string}[]; result_preview?: string };
            setAgentStatuses((prev) => {
              const exists = prev.find((a) => a.id === d.agent_id);
              if (exists) return prev.map((a) => (a.id === d.agent_id ? { ...a, status: d.status, ...(d.tool_calls ? { tool_calls: d.tool_calls } : {}), ...(d.result_preview ? { result_preview: d.result_preview } : {}) } : a));
              return [...prev, { id: d.agent_id, status: d.status, task: d.task, role: d.role }];
            });
          } else if (event.type === "file_diff" && event.data) {
            const d = event.data as unknown as import("@/components/DiffViewer").FileDiff;
            setFileDiffs((prev) => [...prev, d]);
          } else if (event.type === "files_changed" && event.data) {
            const d = event.data as { files: string[]; count: number };
            const fileList = d.files.map((f: string) => `  • ${f}`).join("\n");
            setMessages((prev) => [...prev, { role: "assistant", content: `📝 **${d.count} file(s) modified:**\n${fileList}` }]);
          } else if (event.type === "stream_output" && event.data) {
            const d = event.data as { stream: string; data: string };
            const prefix = d.stream === "stderr" ? "[stderr] " : "";
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last?.role === "assistant" && (last as { _streaming_terminal?: boolean })._streaming_terminal) {
                return [...prev.slice(0, -1), { ...last, content: last.content + prefix + d.data }];
              }
              return [...prev, { role: "assistant", content: "```\n" + prefix + d.data, _streaming_terminal: true }];
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
          } else if (event.type === "validation_result" && event.data) {
            const d = event.data as { tool: string; status: "passed" | "failed" | "skipped"; message?: string; strategy?: string };
            setToolCalls((prev) => {
              let updated = false;
              const next = [...prev];
              for (let i = next.length - 1; i >= 0; i -= 1) {
                if (next[i].tool !== d.tool) continue;
                next[i] = {
                  ...next[i],
                  validationStatus: d.status,
                  validationMessage: d.message,
                  validationStrategy: d.strategy,
                };
                updated = true;
                break;
              }
              if (updated) return next;
              return [
                ...prev,
                {
                  tool: d.tool,
                  status: "completed",
                  validationStatus: d.status,
                  validationMessage: d.message,
                  validationStrategy: d.strategy,
                },
              ];
            });
          } else if (event.type === "permission_request" && event.data) {
            const d = event.data as unknown as PermissionPrompt;
            if (d.status === "pending") {
              setPendingPermission(d);
            } else {
              setPendingPermission((current) => current?.request_id === d.request_id ? null : current);
            }
          } else if (event.type === "permission_decision" && event.data) {
            const d = event.data as { request_id?: string };
            setPendingPermission((current) => current?.request_id === d.request_id ? null : current);
          } else if (event.type === "prompt_suggestion" && event.data) {
            const d = event.data as PromptSuggestionEventData;
            if (d.background) {
              setSpeculationRecord(d.background);
            }
          } else if (event.type === "speculation_state" && event.data) {
            setSpeculationRecord(event.data as unknown as SpeculationRecord);
          } else if (event.type === "speculation_hit" && event.data) {
            setSpeculationRecord(event.data as unknown as SpeculationRecord);
          } else if (event.type === "error") {
            collected += `\n\n**Error:** ${buildChatErrorMessage(event.content || "未知错误", selectedModel, models)}`;
            setStreamContent(collected);
          }
        },
        undefined,
        controller.signal,
      );
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") {
        collected += "\n\n*[Stopped by user]*";
      } else {
        const rawMessage = err instanceof Error ? err.message : "Connection error. Please check if the backend is running.";
        collected += `\n\n**Error:** ${buildChatErrorMessage(rawMessage, selectedModel, models)}`;
      }
      setStreamContent(collected);
    } finally {
      if (requestIdRef.current === myRequestId) {
        abortRef.current = null;
      }
      if (permissionPoll) {
        clearInterval(permissionPoll);
      }
    }

    // Only update state if this is still the active request
    // (prevents stale cleanup from overriding a newer request after Stop → resend)
    if (requestIdRef.current !== myRequestId) return;

    setMessages((prev) => [
      ...prev,
      { role: "assistant", content: collected || "No response", ...(usageData ? { usage: usageData } : {}) },
    ]);
    if (collected) extractHtmlPreview(collected);
    if (latestThreadId) {
      void loadSpeculation(latestThreadId);
    }
    setStreamContent("");
    setStreaming(false);
    setAgentStatuses([]);
  }, [streaming, activeThreadId, selectedModel, selectedSkillNames, selectedToolNames, agentMode, loadSpeculation, models]);

  const handleUseSuggestion = useCallback(() => {
    if (!speculationRecord?.suggestion) return;
    setInput(speculationRecord.suggestion);
    inputRef.current?.focus();
  }, [speculationRecord]);

  const handleSend = useCallback(async () => {
    if ((!input.trim() && pendingImages.length === 0) || streaming) return;
    const content = input.trim() || "请看这张图片";

    // Intercept slash commands — dispatch locally, don't hit the LLM
    if (isSlashCommand(content)) {
      setInput("");
      setMessages((prev) => [...prev, { role: "user", content }]);
      const { output } = await dispatchSlashCommand(content);
      setMessages((prev) => [...prev, { role: "assistant", content: output }]);
      return;
    }

    const imgs = pendingImages.length > 0 ? [...pendingImages] : undefined;
    setMessages((prev) => [...prev, { role: "user", content: imgs ? `${content}\n\n[${imgs.length} image(s) attached]` : content }]);
    setInput("");
    setPendingImages([]);
    // Refresh thread list after a short delay so the newly saved user message is searchable
    setTimeout(() => loadThreads(), 1000);
    await doSend(content, imgs);
  }, [input, streaming, doSend, pendingImages]);

  const handleRegenerate = useCallback(async () => {
    if (streaming || messages.length < 2) return;
    const lastUserMsg = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUserMsg) return;
    setMessages((prev) => {
      const lastAssistantIdx = prev.map((m) => m.role).lastIndexOf("assistant");
      if (lastAssistantIdx >= 0) return prev.slice(0, lastAssistantIdx);
      return prev;
    });
    await doSend(lastUserMsg.content);
  }, [streaming, messages, doSend]);

  const handleExport = useCallback(() => {
    if (messages.length === 0) return;
    const threadTitle = threads.find((t) => t.id === activeThreadId)?.title || "chat";
    const md = messages
      .map((m) => `## ${m.role === "user" ? "You" : "Super Agent"}\n\n${m.content}`)
      .join("\n\n---\n\n");
    const blob = new Blob([md], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${threadTitle}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }, [messages, threads, activeThreadId]);

  const handleEditSubmit = useCallback(async (index: number) => {
    if (!editingContent.trim() || streaming) return;
    const content = editingContent.trim();
    setMessages((prev) => [...prev.slice(0, index), { role: "user", content }]);
    setEditingIndex(null);
    setEditingContent("");
    await doSend(content);
  }, [editingContent, streaming, doSend]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const toggleDark = () => {
    const next = !darkMode;
    setDarkMode(next);
    localStorage.setItem("darkMode", String(next));
    document.documentElement.classList.toggle("dark", next);
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch("/api/upload", { method: "POST", body: formData });
      if (!res.ok) throw new Error(`Upload failed (${res.status})`);
      const data = await res.json();
      setInput((prev) => prev + `\n[已附加文件：${data.filename}（${data.size} 字节）]`);
    } catch (err) {
      alert(`文件上传失败：${err instanceof Error ? err.message : "未知错误"}`);
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const extractHtmlPreview = (content: string) => {
    const htmlMatch = content.match(/```html\s*\n([\s\S]*?)```/);
    if (htmlMatch) {
      setPreviewHtml(htmlMatch[1]);
    }
  };

  const visibleTools = availableTools.filter((tool) => {
    const query = toolSearch.trim().toLowerCase();
    if (!query) return true;
    return `${tool.name} ${tool.display_name || ""} ${TOOL_NAME_LABELS[tool.name] || ""} ${tool.summary || ""} ${tool.description || ""} ${tool.category || ""}`.toLowerCase().includes(query);
  });

  if (showSettingsPage) {
    return (
      <div className="h-screen" style={{ background: "var(--bg-primary)" }}>
        <SettingsPage onBack={() => {
          setShowSettingsPage(false);
          loadModels();
        }} />
      </div>
    );
  }

  return (
    <div className="flex h-screen" style={{ background: "var(--bg-primary)" }}>
      {/* Sidebar overlay for mobile */}
      {sidebarOpen && (
        <div className="fixed inset-0 bg-black/50 z-30 md:hidden" onClick={() => setSidebarOpen(false)} />
      )}

      {/* Sidebar */}
      <ChatSidebar
        threads={threads}
        activeThreadId={activeThreadId}
        threadSearch={threadSearch}
        setThreadSearch={setThreadSearch}
        sidebarOpen={sidebarOpen}
        setSidebarOpen={setSidebarOpen}
        darkMode={darkMode}
        toggleDark={toggleDark}
        handleNewChat={handleNewChat}
        handleSelectThread={handleSelectThread}
        handleDeleteThread={handleDeleteThread}
        loadThreads={loadThreads}
        setShowSettingsPage={setShowSettingsPage}
      />

      {/* Main */}
      <main className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <ChatHeader
          threads={threads}
          activeThreadId={activeThreadId}
          agentMode={agentMode}
          setAgentMode={setAgentMode}
          sidebarOpen={sidebarOpen}
          setSidebarOpen={setSidebarOpen}
          localClients={localClients}
          showLocalPanel={showLocalPanel}
          setShowLocalPanel={setShowLocalPanel}
          messages={messages}
          streaming={streaming}
          models={models}
          selectedModel={selectedModel}
          setSelectedModel={setSelectedModel}
          showMonitorPanel={showMonitorPanel}
          setShowMonitorPanel={setShowMonitorPanel}
          speculationEnabled={speculationEnabled}
          toggleSpeculation={toggleSpeculation}
          handleRegenerate={handleRegenerate}
          handleExport={handleExport}
          handleStop={handleStop}
        />

        {/* Messages */}
        <MessageArea
          messages={messages}
          streaming={streaming}
          streamContent={streamContent}
          agentMode={agentMode}
          localClients={localClients}
          localDisconnectNotice={localDisconnectNotice}
          setLocalDisconnectNotice={setLocalDisconnectNotice}
          skills={skills}
          setInput={setInput}
          setAgentMode={setAgentMode}
          editingIndex={editingIndex}
          setEditingIndex={setEditingIndex}
          editingContent={editingContent}
          setEditingContent={setEditingContent}
          handleEditSubmit={handleEditSubmit}
          toolCalls={toolCalls}
          fileDiffs={fileDiffs}
          pendingPermission={pendingPermission}
          resolvePendingPermission={resolvePendingPermission}
          agentStatuses={agentStatuses}
          lastUsage={lastUsage}
          speculationEnabled={speculationEnabled}
          speculationRecord={speculationRecord}
          speculationDiffs={speculationDiffs}
          speculationDiffLoading={speculationDiffLoading}
          speculationBusyAction={speculationBusyAction}
          speculationNotice={speculationNotice}
          handleUseSuggestion={handleUseSuggestion}
          handleRefreshSpeculation={handleRefreshSpeculation}
          handleAcceptSpeculation={handleAcceptSpeculation}
          handleDiscardSpeculation={handleDiscardSpeculation}
          messagesEndRef={messagesEndRef}
        />

        {/* Web Preview Panel */}
        {previewHtml && (
          <WebPreviewPanel html={previewHtml} onClose={() => setPreviewHtml(null)} />
        )}

        {/* Input */}
        <ChatInput
          input={input}
          setInput={setInput}
          pendingImages={pendingImages}
          setPendingImages={setPendingImages}
          streaming={streaming}
          agentMode={agentMode}
          slashCompletions={slashCompletions}
          setSlashCompletions={setSlashCompletions}
          slashSelectedIdx={slashSelectedIdx}
          setSlashSelectedIdx={setSlashSelectedIdx}
          getSlashCompletions={getSlashCompletions}
          skills={skills}
          selectedSkillNames={selectedSkillNames}
          toggleSelectedSkill={toggleSelectedSkill}
          setSelectedSkillNames={setSelectedSkillNames}
          showSkillPanel={showSkillPanel}
          setShowSkillPanel={setShowSkillPanel}
          availableTools={availableTools}
          visibleTools={visibleTools}
          selectedToolNames={selectedToolNames}
          toggleSelectedTool={toggleSelectedTool}
          setSelectedToolNames={setSelectedToolNames}
          showToolPanel={showToolPanel}
          setShowToolPanel={setShowToolPanel}
          toolSearch={toolSearch}
          setToolSearch={setToolSearch}
          handleSend={handleSend}
          handleStop={handleStop}
          handleKeyDown={handleKeyDown}
          handleFileUpload={handleFileUpload}
          inputRef={inputRef}
          fileInputRef={fileInputRef}
        />
      </main>

      {/* Workspace Panel */}
      <WorkspacePanel threadId={activeThreadId} refreshToken={workspaceRefreshToken} onPreviewHtml={(html) => setPreviewHtml(html)} />

      {showMonitorPanel && (
        <div className="w-[420px] max-w-[42vw] flex-shrink-0 flex flex-col border-l" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
          <div className="p-3 border-b flex items-center justify-between" style={{ borderColor: "var(--border-color)" }}>
            <div className="flex items-center gap-2">
              <Activity className="w-4 h-4" style={{ color: "var(--accent)" }} />
              <span className="text-sm font-semibold">后台监控</span>
            </div>
            <button onClick={() => setShowMonitorPanel(false)} className="text-xs" style={{ color: "var(--text-secondary)" }}>关闭</button>
          </div>
          <ToolMonitorPanel compact activeThreadId={activeThreadId} threads={threads} />
        </div>
      )}

      {/* Local Mode Panel */}
      {showLocalPanel && agentMode === "local" && (
        <LocalPanel
          localClients={localClients}
          localAuditLog={localAuditLog}
          toolStats={toolStats}
          localShortcuts={localShortcuts}
          localSchedules={localSchedules}
          localPanelMessage={localPanelMessage}
          activeThreadId={activeThreadId}
          setShowLocalPanel={setShowLocalPanel}
          handleBindLocalThread={handleBindLocalThread}
          handleSetLocalAutoApprove={handleSetLocalAutoApprove}
          handleSetLocalToolPermission={handleSetLocalToolPermission}
          loadLocalClients={loadLocalClients}
          loadLocalAuditLog={loadLocalAuditLog}
          loadToolStats={loadToolStats}
          loadShortcuts={loadShortcuts}
          loadSchedules={loadSchedules}
          setInput={setInput}
        />
      )}
    </div>
  );
}
