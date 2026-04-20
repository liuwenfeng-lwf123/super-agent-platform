"use client";

import { useState, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import {
  ArrowLeft,
  Bot,
  Key,
  Brain,
  Wrench,
  Globe,
  Plus,
  Trash2,
  Activity,
  Server,
} from "lucide-react";
import { fetchModels, fetchSkills, fetchMemory, addMemory, deleteMemory } from "@/lib/api";
import type { ModelConfig, SkillConfig, MemoryEntry } from "@/types";

interface Props {
  onBack: () => void;
}

export default function SettingsPanel({ onBack }: Props) {
  const [tab, setTab] = useState<"models" | "memory" | "skills" | "mcp" | "tracing">("models");
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [skills, setSkills] = useState<SkillConfig[]>([]);
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [mcpServers, setMcpServers] = useState<any[]>([]);
  const [mcpTools, setMcpTools] = useState<any[]>([]);
  const [tracing, setTracing] = useState<any>({});
  const [newMemKey, setNewMemKey] = useState("");
  const [newMemVal, setNewMemVal] = useState("");
  const [newMcpName, setNewMcpName] = useState("");
  const [newMcpUrl, setNewMcpUrl] = useState("");
  const [newModelName, setNewModelName] = useState("");
  const [newModelDisplay, setNewModelDisplay] = useState("");
  const [newModelBaseUrl, setNewModelBaseUrl] = useState("");

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      const [m, s, mem] = await Promise.all([fetchModels(), fetchSkills(), fetchMemory()]);
      setModels(m);
      setSkills(s);
      setMemories(mem);
    } catch {}
    loadMcp();
    loadTracing();
  };

  const loadMcp = async () => {
    try {
      const [servers, tools] = await Promise.all([
        fetch("/api/mcp/servers").then(r => r.json()),
        fetch("/api/mcp/tools").then(r => r.json()),
      ]);
      setMcpServers(servers);
      setMcpTools(tools);
    } catch {}
  };

  const loadTracing = async () => {
    try {
      const r = await fetch("/api/tracing/status");
      setTracing(await r.json());
    } catch {}
  };

  const handleAddMemory = async () => {
    if (!newMemKey || !newMemVal) return;
    await addMemory(newMemKey, newMemVal, "knowledge");
    setNewMemKey("");
    setNewMemVal("");
    const mem = await fetchMemory();
    setMemories(mem);
  };

  const handleDeleteMemory = async (id: string) => {
    await deleteMemory(id);
    const mem = await fetchMemory();
    setMemories(mem);
  };

  const handleAddMcp = async () => {
    if (!newMcpName || !newMcpUrl) return;
    await fetch(`/api/mcp/servers?name=${encodeURIComponent(newMcpName)}&url=${encodeURIComponent(newMcpUrl)}`, { method: "POST" });
    setNewMcpName("");
    setNewMcpUrl("");
    loadMcp();
  };

  const handleAddModel = async () => {
    if (!newModelName || !newModelDisplay) return;
    const params = new URLSearchParams({
      name: newModelName,
      display_name: newModelDisplay,
      model: newModelName,
    });
    if (newModelBaseUrl) params.append("base_url", newModelBaseUrl);
    await fetch(`/api/models?${params}`, { method: "POST" });
    setNewModelName("");
    setNewModelDisplay("");
    setNewModelBaseUrl("");
    const m = await fetchModels();
    setModels(m);
  };

  const handleDeleteModel = async (name: string) => {
    await fetch(`/api/models/${encodeURIComponent(name)}`, { method: "DELETE" });
    const m = await fetchModels();
    setModels(m);
  };

  const handleDeleteMcp = async (name: string) => {
    await fetch(`/api/mcp/servers/${encodeURIComponent(name)}`, { method: "DELETE" });
    loadMcp();
  };

  const tabs = [
    { key: "models" as const, label: "Models", icon: Bot },
    { key: "memory" as const, label: "Memory", icon: Brain },
    { key: "skills" as const, label: "Skills", icon: Wrench },
    { key: "mcp" as const, label: "MCP Servers", icon: Server },
    { key: "tracing" as const, label: "Tracing", icon: Activity },
  ];

  return (
    <div className="h-full flex flex-col" style={{ background: "var(--bg-primary)" }}>
      {/* Header */}
      <div className="h-14 flex items-center gap-3 px-6 border-b" style={{ borderColor: "var(--border-color)" }}>
        <button onClick={onBack} className="p-2 rounded-lg hover:opacity-80" style={{ color: "var(--text-secondary)" }}>
          <ArrowLeft className="w-4 h-4" />
        </button>
        <h1 className="text-sm font-semibold">Settings</h1>
      </div>

      <div className="flex-1 flex min-h-0">
        {/* Tabs */}
        <div className="w-48 border-r p-3 space-y-1" style={{ borderColor: "var(--border-color)" }}>
          {tabs.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-left transition-colors"
              style={{
                background: tab === t.key ? "var(--accent-light)" : "transparent",
                color: tab === t.key ? "var(--accent)" : "var(--text-secondary)",
              }}
            >
              <t.icon className="w-4 h-4" />
              {t.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 scrollbar-thin">
          {/* Models */}
          {tab === "models" && (
            <div className="max-w-2xl space-y-4">
              <h2 className="text-lg font-semibold">Model Configuration</h2>
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                Configure LLM models. API Key is set via environment variable.
              </p>
              {models.map(m => (
                <div key={m.name} className="p-4 rounded-xl border" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-medium text-sm">{m.display_name}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-xs px-2 py-0.5 rounded-full" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>
                        {m.provider}
                      </span>
                      <button
                        onClick={() => handleDeleteModel(m.name)}
                        className="p-1 rounded hover:text-red-400"
                        style={{ color: "var(--text-secondary)" }}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                  <div className="text-xs space-y-1" style={{ color: "var(--text-secondary)" }}>
                    <p>Model: <code className="px-1 rounded" style={{ background: "var(--bg-primary)" }}>{m.model}</code></p>
                    <p>Base URL: <code className="px-1 rounded" style={{ background: "var(--bg-primary)" }}>{m.base_url}</code></p>
                  </div>
                </div>
              ))}

              <div className="p-4 rounded-xl border" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                <h3 className="text-sm font-medium mb-3">Add Custom Model</h3>
                <div className="space-y-2">
                  <input
                    value={newModelName}
                    onChange={e => setNewModelName(e.target.value)}
                    placeholder="Model name (e.g., gpt-4o)"
                    className="w-full px-3 py-2 rounded-lg border text-sm outline-none"
                    style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                  />
                  <input
                    value={newModelDisplay}
                    onChange={e => setNewModelDisplay(e.target.value)}
                    placeholder="Display name (e.g., GPT-4o)"
                    className="w-full px-3 py-2 rounded-lg border text-sm outline-none"
                    style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                  />
                  <input
                    value={newModelBaseUrl}
                    onChange={e => setNewModelBaseUrl(e.target.value)}
                    placeholder="Base URL (optional, defaults to env OPENAI_BASE_URL)"
                    className="w-full px-3 py-2 rounded-lg border text-sm outline-none"
                    style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                  />
                  <button
                    onClick={handleAddModel}
                    disabled={!newModelName || !newModelDisplay}
                    className="flex items-center gap-1 px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-30"
                    style={{ background: "var(--accent)", color: "#fff" }}
                  >
                    <Plus className="w-4 h-4" /> Add Model
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Memory */}
          {tab === "memory" && (
            <div className="max-w-2xl space-y-4">
              <h2 className="text-lg font-semibold">Long-term Memory</h2>
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                Persistent memory across conversations.
              </p>
              {/* Add */}
              <div className="flex gap-2">
                <input
                  value={newMemKey}
                  onChange={e => setNewMemKey(e.target.value)}
                  placeholder="Key"
                  className="flex-1 px-3 py-2 rounded-lg border text-sm outline-none"
                  style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                />
                <input
                  value={newMemVal}
                  onChange={e => setNewMemVal(e.target.value)}
                  placeholder="Value"
                  className="flex-1 px-3 py-2 rounded-lg border text-sm outline-none"
                  style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                />
                <button onClick={handleAddMemory} className="p-2 rounded-lg" style={{ background: "var(--accent)", color: "#fff" }}>
                  <Plus className="w-4 h-4" />
                </button>
              </div>
              {/* List */}
              {memories.map(m => (
                <div key={m.id} className="p-4 rounded-xl border flex items-start justify-between gap-3" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                  <div>
                    <div className="font-medium text-sm">{m.key}</div>
                    <div className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>{m.value}</div>
                    <span className="text-xs mt-1 inline-block px-2 py-0.5 rounded-full" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>
                      {m.category}
                    </span>
                  </div>
                  <button onClick={() => handleDeleteMemory(m.id)} className="p-1 rounded hover:text-red-400" style={{ color: "var(--text-secondary)" }}>
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ))}
              {memories.length === 0 && <p className="text-sm text-center py-4" style={{ color: "var(--text-secondary)" }}>No memory entries yet</p>}
            </div>
          )}

          {/* Skills */}
          {tab === "skills" && (
            <div className="max-w-2xl space-y-4">
              <h2 className="text-lg font-semibold">Skills</h2>
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                Built-in and custom skills for the agent.
              </p>
              {skills.map(s => (
                <div key={s.name} className="p-4 rounded-xl border" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-medium text-sm">{s.display_name}</span>
                    <div className="flex items-center gap-2">
                      {s.built_in && <span className="text-xs px-2 py-0.5 rounded-full" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>Built-in</span>}
                      <span className={`text-xs px-2 py-0.5 rounded-full ${s.enabled ? "bg-green-900 text-green-300" : "bg-red-900 text-red-300"}`}>
                        {s.enabled ? "Enabled" : "Disabled"}
                      </span>
                    </div>
                  </div>
                  <p className="text-xs" style={{ color: "var(--text-secondary)" }}>{s.description}</p>
                </div>
              ))}
            </div>
          )}

          {/* MCP */}
          {tab === "mcp" && (
            <div className="max-w-2xl space-y-4">
              <h2 className="text-lg font-semibold">MCP Servers</h2>
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                Connect external tools via Model Context Protocol.
              </p>
              <div className="flex gap-2">
                <input
                  value={newMcpName}
                  onChange={e => setNewMcpName(e.target.value)}
                  placeholder="Server name"
                  className="flex-1 px-3 py-2 rounded-lg border text-sm outline-none"
                  style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                />
                <input
                  value={newMcpUrl}
                  onChange={e => setNewMcpUrl(e.target.value)}
                  placeholder="http://localhost:3001"
                  className="flex-1 px-3 py-2 rounded-lg border text-sm outline-none"
                  style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                />
                <button onClick={handleAddMcp} className="p-2 rounded-lg" style={{ background: "var(--accent)", color: "#fff" }}>
                  <Plus className="w-4 h-4" />
                </button>
              </div>
              {mcpServers.map(s => (
                <div key={s.name} className="p-4 rounded-xl border flex items-start justify-between gap-3" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                  <div>
                    <div className="font-medium text-sm">{s.name}</div>
                    <div className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>{s.url}</div>
                    {s.tools.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-2">
                        {s.tools.map((t: string) => (
                          <span key={t} className="text-xs px-2 py-0.5 rounded-full" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>{t}</span>
                        ))}
                      </div>
                    )}
                  </div>
                  <button onClick={() => handleDeleteMcp(s.name)} className="p-1 rounded hover:text-red-400" style={{ color: "var(--text-secondary)" }}>
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ))}
              {mcpServers.length === 0 && <p className="text-sm text-center py-4" style={{ color: "var(--text-secondary)" }}>No MCP servers configured</p>}

              {mcpTools.length > 0 && (
                <>
                  <h3 className="text-sm font-semibold mt-4">Available Tools</h3>
                  {mcpTools.map((t, i) => (
                    <div key={i} className="p-3 rounded-lg border text-sm" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                      <span className="font-medium">{t.name}</span>
                      <span className="ml-2 text-xs" style={{ color: "var(--text-secondary)" }}>from {t.server}</span>
                      <p className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>{t.description}</p>
                    </div>
                  ))}
                </>
              )}
            </div>
          )}

          {/* Tracing */}
          {tab === "tracing" && (
            <div className="max-w-2xl space-y-4">
              <h2 className="text-lg font-semibold">Observability & Tracing</h2>
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                Monitor LLM calls and agent execution traces.
              </p>
              {["langsmith", "langfuse"].map(p => {
                const info = tracing[p] || {};
                return (
                  <div key={p} className="p-4 rounded-xl border" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-sm capitalize">{p}</span>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${info.enabled ? "bg-green-900 text-green-300" : "bg-gray-700 text-gray-400"}`}>
                        {info.enabled ? "Active" : "Inactive"}
                      </span>
                    </div>
                    <p className="text-xs mt-2" style={{ color: "var(--text-secondary)" }}>
                      {info.enabled
                        ? p === "langsmith"
                          ? `Project: ${info.project || "default"}`
                          : "Public key configured"
                        : `Set ${p.toUpperCase()}_* env vars and restart backend`}
                    </p>
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
