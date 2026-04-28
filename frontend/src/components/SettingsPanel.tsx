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
  BookOpen,
  Upload,
  Sparkles,
  Shield,
} from "lucide-react";
import {
  fetchModels,
  fetchSkills,
  fetchMemory,
  addMemory,
  deleteMemory,
  seedDemoData,
  fetchSecurityPolicy,
  fetchSecurityAudit,
  saveProviderApiKey,
  fetchProviderKeys,
  deleteProviderApiKey,
  fetchFeatures,
  toggleFeature,
} from "@/lib/api";
import type { FeatureInfo } from "@/lib/api";
import type { DemoSeedResult, ModelConfig, SecurityAuditEvent, SecurityPolicyResponse, SkillConfig, MemoryEntry } from "@/types";

interface Props {
  onBack: () => void;
}

export default function SettingsPanel({ onBack }: Props) {
  const [tab, setTab] = useState<"models" | "memory" | "skills" | "mcp" | "knowledge" | "demo" | "security" | "tracing" | "features">("features");
  const [features, setFeatures] = useState<FeatureInfo[]>([]);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [toggleMsg, setToggleMsg] = useState<string | null>(null);
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
  const [newModelProvider, setNewModelProvider] = useState("openai");
  const [providers, setProviders] = useState<{ name: string; api_key_env: string; base_url: string }[]>([]);
  const [apiProvider, setApiProvider] = useState("openai");
  const [apiKey, setApiKey] = useState("");
  const [apiKeyLabel, setApiKeyLabel] = useState("default");
  const [apiKeySaving, setApiKeySaving] = useState(false);
  const [apiKeyMessage, setApiKeyMessage] = useState("");
  const [apiKeyMessageType, setApiKeyMessageType] = useState<"success" | "error">("success");
  const [providerKeyCounts, setProviderKeyCounts] = useState<Record<string, number>>({});
  const [providerKeys, setProviderKeys] = useState<Record<string, { label: string; is_disabled: boolean; last_used: number }[]>>({});
  const [kbDocs, setKbDocs] = useState<any[]>([]);
  const [kbSearch, setKbSearch] = useState("");
  const [kbResults, setKbResults] = useState<any[]>([]);
  const [demoClean, setDemoClean] = useState(true);
  const [demoDryRun, setDemoDryRun] = useState(false);
  const [demoLoading, setDemoLoading] = useState(false);
  const [demoResult, setDemoResult] = useState<DemoSeedResult | null>(null);
  const [demoError, setDemoError] = useState("");
  const [securityPolicy, setSecurityPolicy] = useState<SecurityPolicyResponse | null>(null);
  const [securityAudit, setSecurityAudit] = useState<SecurityAuditEvent[]>([]);
  const [securityLoading, setSecurityLoading] = useState(false);
  const [securityError, setSecurityError] = useState("");

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
    loadProviders();
    loadTracing();
    loadKnowledge();
    loadSecurity();
    loadFeatures();
  };

  const loadFeatures = async () => {
    try {
      const data = await fetchFeatures();
      setFeatures(data.features || []);
    } catch {
      setFeatures([]);
    }
  };

  const loadKnowledge = async () => {
    try {
      const res = await fetch("/api/knowledge");
      const docs = await res.json();
      setKbDocs(Array.isArray(docs) ? docs : []);
    } catch {
      setKbDocs([]);
    }
  };

  const handleKbUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    try {
      await fetch("/api/knowledge/upload", { method: "POST", body: formData });
      loadKnowledge();
    } catch {}
    e.target.value = "";
  };

  const handleKbDelete = async (docId: string) => {
    await fetch(`/api/knowledge/${docId}`, { method: "DELETE" });
    loadKnowledge();
  };

  const handleKbSearch = async () => {
    if (!kbSearch.trim()) return;
    try {
      const results = await fetch(`/api/knowledge/search?query=${encodeURIComponent(kbSearch)}`).then(r => r.json());
      setKbResults(results);
    } catch {}
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

  const loadProviders = async () => {
    try {
      const res = await fetch("/api/providers");
      const data = await res.json();
      const nextProviders = Array.isArray(data.providers) ? data.providers : [];
      setProviders(nextProviders);
      const counts: Record<string, number> = {};
      const nextKeysByProvider: Record<string, { label: string; is_disabled: boolean; last_used: number }[]> = {};
      await Promise.all(nextProviders.map(async (provider: { name: string }) => {
        try {
          const keys = await fetchProviderKeys(provider.name);
          const providerKeys = Array.isArray(keys.keys) ? keys.keys : [];
          nextKeysByProvider[provider.name] = providerKeys;
          counts[provider.name] = providerKeys.length;
        } catch {
          nextKeysByProvider[provider.name] = [];
          counts[provider.name] = 0;
        }
      }));
      setProviderKeyCounts(counts);
      setProviderKeys(nextKeysByProvider);
    } catch {
      setProviders([]);
      setProviderKeyCounts({});
      setProviderKeys({});
    }
  };

  const loadTracing = async () => {
    try {
      const r = await fetch("/api/tracing/status");
      setTracing(await r.json());
    } catch {}
  };

  const loadSecurity = async () => {
    setSecurityLoading(true);
    setSecurityError("");
    try {
      const [policy, audit] = await Promise.all([
        fetchSecurityPolicy(),
        fetchSecurityAudit({ limit: 30 }),
      ]);
      setSecurityPolicy(policy);
      setSecurityAudit(Array.isArray(audit.events) ? audit.events : []);
    } catch (error) {
      setSecurityError(error instanceof Error ? error.message : "加载安全策略失败");
    } finally {
      setSecurityLoading(false);
    }
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
    if (newModelProvider) params.append("provider", newModelProvider);
    await fetch(`/api/models?${params}`, { method: "POST" });
    setNewModelName("");
    setNewModelDisplay("");
    setNewModelBaseUrl("");
    setNewModelProvider("openai");
    const m = await fetchModels();
    setModels(m);
  };

  const handleSaveApiKey = async () => {
    if (!apiKey.trim()) {
      setApiKeyMessageType("error");
      setApiKeyMessage("请先粘贴 API Key。");
      return;
    }
    setApiKeySaving(true);
    setApiKeyMessage("");
    try {
      const result = await saveProviderApiKey(apiProvider, apiKey.trim(), apiKeyLabel.trim() || "default");
      if (result.status !== "added") {
        setApiKeyMessageType("error");
        setApiKeyMessage(result.message || "保存失败，请检查后端日志。");
        return;
      }
      setApiKey("");
      setApiKeyMessageType("success");
      setApiKeyMessage(`已加密保存 ${apiProvider} 的 API Key。刷新模型状态中...`);
      await Promise.all([loadProviders(), fetchModels().then(setModels)]);
      setApiKeyMessage(`已加密保存 ${apiProvider} 的 API Key，可以直接开始对话。`);
    } catch (error) {
      setApiKeyMessageType("error");
      setApiKeyMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setApiKeySaving(false);
    }
  };

  const handleDeleteApiKey = async (provider: string, label: string) => {
    const result = await deleteProviderApiKey(provider, label);
    if (!result.ok) {
      setApiKeyMessageType("error");
      setApiKeyMessage(`删除 ${provider}/${label} 失败。`);
      return;
    }
    setApiKeyMessageType("success");
    setApiKeyMessage(`已删除 ${provider}/${label}，请保存正确的 API Key 后再对话。`);
    await Promise.all([loadProviders(), fetchModels().then(setModels)]);
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

  const handleSeedDemo = async () => {
    setDemoLoading(true);
    setDemoError("");
    try {
      const result = await seedDemoData({ clean: demoClean, dryRun: demoDryRun });
      setDemoResult(result);
      if (!demoDryRun) {
        const mem = await fetchMemory();
        setMemories(mem);
        loadKnowledge();
      }
    } catch (error) {
      setDemoError(error instanceof Error ? error.message : "初始化演示数据失败");
    } finally {
      setDemoLoading(false);
    }
  };

  const tabs = [
    { key: "features" as const, label: "✨ 新功能", icon: Sparkles },
    { key: "models" as const, label: "模型", icon: Bot },
    { key: "memory" as const, label: "记忆", icon: Brain },
    { key: "skills" as const, label: "技能", icon: Wrench },
    { key: "mcp" as const, label: "MCP 服务", icon: Server },
    { key: "knowledge" as const, label: "知识库", icon: BookOpen },
    { key: "demo" as const, label: "演示", icon: Sparkles },
    { key: "security" as const, label: "安全", icon: Shield },
    { key: "tracing" as const, label: "追踪", icon: Activity },
  ];

  const riskLabels: Record<string, string> = {
    safe: "低风险",
    approval_required: "需审批",
    dangerous: "高风险",
    disabled: "已禁用",
  };
  const decisionClasses: Record<string, string> = {
    allow: "bg-green-900 text-green-300",
    ask: "bg-yellow-900 text-yellow-300",
    deny: "bg-red-900 text-red-300",
  };
  const apiKeySourceLabels: Record<string, string> = {
    credential_store: "设置页保存",
    oauth: "OAuth",
    environment: "环境变量",
    settings: ".env",
    none: "未配置",
  };

  return (
    <div className="h-full flex flex-col" style={{ background: "var(--bg-primary)" }}>
      {/* Header */}
      <div className="h-14 flex items-center gap-3 px-6 border-b" style={{ borderColor: "var(--border-color)" }}>
        <button onClick={onBack} className="p-2 rounded-lg hover:opacity-80" style={{ color: "var(--text-secondary)" }}>
          <ArrowLeft className="w-4 h-4" />
        </button>
        <h1 className="text-sm font-semibold">设置</h1>
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
          {/* Features */}
          {tab === "features" && (
            <div className="max-w-2xl space-y-4">
              <h2 className="text-lg font-semibold">✨ 新功能总览</h2>
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                以下是最近新增的所有平台能力。可开启的功能点击开关即可切换。
              </p>
              {toggleMsg && (
                <div className="text-xs px-3 py-2 rounded-lg border border-green-800 bg-green-900/20 text-green-300">
                  {toggleMsg}
                </div>
              )}
              {features.length === 0 && (
                <p className="text-xs" style={{ color: "var(--text-secondary)" }}>加载中...</p>
              )}
              {features.map((f) => {
                const isActive = f.status === "active";
                const canToggle = f.toggleable === true;
                return (
                  <div
                    key={f.id}
                    className="p-4 rounded-xl border"
                    style={{
                      borderColor: isActive ? "rgba(34,197,94,0.4)" : f.status === "available" ? "rgba(59,130,246,0.4)" : "var(--border-color)",
                      background: isActive ? "rgba(34,197,94,0.06)" : f.status === "available" ? "rgba(59,130,246,0.06)" : "var(--bg-secondary)",
                    }}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-medium text-sm">{f.name}</span>
                      <div className="flex items-center gap-2">
                        {canToggle ? (
                          <button
                            disabled={togglingId === f.id}
                            onClick={async () => {
                              setTogglingId(f.id);
                              setToggleMsg(null);
                              try {
                                const res = await toggleFeature(f.id, !isActive);
                                setToggleMsg(res.message || (isActive ? "已关闭" : "已开启"));
                                await loadFeatures();
                              } catch {
                                setToggleMsg("操作失败，请重试");
                              } finally {
                                setTogglingId(null);
                              }
                            }}
                            className="relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none"
                            style={{
                              background: isActive ? "#22c55e" : "#374151",
                              opacity: togglingId === f.id ? 0.5 : 1,
                              cursor: togglingId === f.id ? "wait" : "pointer",
                            }}
                          >
                            <span
                              className="inline-block h-4 w-4 rounded-full bg-white transition-transform"
                              style={{ transform: isActive ? "translateX(22px)" : "translateX(4px)" }}
                            />
                          </button>
                        ) : (
                          <span
                            className={`text-xs px-2 py-0.5 rounded-full ${
                              isActive
                                ? "bg-green-900 text-green-300"
                                : f.status === "available"
                                ? "bg-blue-900 text-blue-300"
                                : "bg-gray-700 text-gray-400"
                            }`}
                          >
                            {isActive ? "已启用" : f.status === "available" ? "可开启" : "暂不可用"}
                          </span>
                        )}
                      </div>
                    </div>
                    <p className="text-xs mb-2" style={{ color: "var(--text-secondary)" }}>
                      {f.description}
                    </p>
                    <div className="text-xs px-3 py-2 rounded-lg font-mono" style={{ background: "rgba(0,0,0,0.3)", color: "var(--text-secondary)" }}>
                      💡 {f.how_to_use}
                    </div>
                    {f.config && Object.keys(f.config).length > 0 && (
                      <div className="mt-2 flex gap-2 flex-wrap">
                        {Object.entries(f.config).map(([k, v]) => (
                          <span key={k} className="text-[10px] px-2 py-0.5 rounded bg-zinc-800 text-zinc-400">
                            {k}: {String(v)}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Models */}
          {tab === "models" && (
            <div className="max-w-2xl space-y-4">
              <h2 className="text-lg font-semibold">模型配置</h2>
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                先保存 API Key，再选择或添加模型。API Key 会加密保存在后端凭证库，不会在前端明文展示。
              </p>

              <div className="p-4 rounded-xl border space-y-3" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-medium flex items-center gap-2"><Key className="w-4 h-4" /> API Key</h3>
                    <p className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>
                      Galaxy、OpenAI 兼容网关请选择 <code>openai</code>，ModelScope/Qwen 请选择 <code>modelscope</code>。
                    </p>
                  </div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                  <select
                    value={apiProvider}
                    onChange={e => setApiProvider(e.target.value)}
                    className="px-3 py-2 rounded-lg border text-sm outline-none"
                    style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                  >
                    {(providers.length ? providers : [{ name: "openai", api_key_env: "OPENAI_API_KEY", base_url: "" }, { name: "modelscope", api_key_env: "MODELSCOPE_API_KEY", base_url: "" }]).map(provider => (
                      <option key={provider.name} value={provider.name}>{provider.name}</option>
                    ))}
                  </select>
                  <input
                    value={apiKeyLabel}
                    onChange={e => setApiKeyLabel(e.target.value)}
                    placeholder="标签（如 galaxy）"
                    className="px-3 py-2 rounded-lg border text-sm outline-none"
                    style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                  />
                  <input
                    value={apiKey}
                    onChange={e => setApiKey(e.target.value)}
                    type="password"
                    placeholder="粘贴 API Key"
                    className="px-3 py-2 rounded-lg border text-sm outline-none"
                    style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                  />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    onClick={handleSaveApiKey}
                    disabled={apiKeySaving || !apiKey.trim()}
                    className="px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-30"
                    style={{ background: "var(--accent)", color: "#fff" }}
                  >
                    {apiKeySaving ? "保存中..." : "加密保存 API Key"}
                  </button>
                  <button
                    onClick={() => {
                      setNewModelProvider("openai");
                      setNewModelName("gpt-5.5");
                      setNewModelDisplay("Galaxy gpt-5.5");
                      setNewModelBaseUrl("https://gpt.eacase.de5.net/v1");
                    }}
                    className="px-3 py-2 rounded-lg border text-sm"
                    style={{ borderColor: "var(--border-color)", color: "var(--text-secondary)" }}
                  >
                    填入 Galaxy 模型示例
                  </button>
                  {apiKeyMessage && <span className="text-xs" style={{ color: apiKeyMessageType === "error" ? "#ef4444" : "#22c55e" }}>{apiKeyMessage}</span>}
                </div>
                <div className="flex flex-wrap gap-2 text-xs" style={{ color: "var(--text-secondary)" }}>
                  {providers.map(provider => (
                    <span key={provider.name} className="px-2 py-1 rounded-full" style={{ background: "var(--bg-primary)" }}>
                      {provider.name}: {providerKeyCounts[provider.name] || 0} 个已保存 key · 环境变量 {provider.api_key_env}
                    </span>
                  ))}
                </div>
                <div className="space-y-2">
                  <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
                    已保存 Key 不会显示明文。如果聊天返回 401/INVALID_API_KEY，请删除对应 provider 的错误 Key 后重新保存。
                  </p>
                  {providers.flatMap(provider => (providerKeys[provider.name] || []).map(key => (
                    <div
                      key={`${provider.name}-${key.label}`}
                      className="flex items-center justify-between gap-2 rounded-lg px-3 py-2 text-xs"
                      style={{ background: "var(--bg-primary)", color: "var(--text-secondary)" }}
                    >
                      <span>{provider.name} / {key.label}{key.is_disabled ? "（已禁用）" : ""}</span>
                      <button
                        onClick={() => handleDeleteApiKey(provider.name, key.label)}
                        className="px-2 py-1 rounded border hover:text-red-400"
                        style={{ borderColor: "var(--border-color)" }}
                      >
                        删除错误 Key
                      </button>
                    </div>
                  )))}
                </div>
              </div>

              {models.map(m => (
                <div key={m.name} className="p-4 rounded-xl border" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-medium text-sm">{m.display_name}</span>
                    <div className="flex items-center gap-2">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${m.has_api_key ? "bg-green-900 text-green-300" : "bg-red-900 text-red-300"}`}>
                        {m.has_api_key ? "Key 已配置" : "缺少 Key"}
                      </span>
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
                    <p>模型：<code className="px-1 rounded" style={{ background: "var(--bg-primary)" }}>{m.model}</code></p>
                    <p>接口地址：<code className="px-1 rounded" style={{ background: "var(--bg-primary)" }}>{m.base_url}</code></p>
                    <p>需要 Key：<code className="px-1 rounded" style={{ background: "var(--bg-primary)" }}>{m.api_key_env}</code></p>
                    <p>Key 来源：<code className="px-1 rounded" style={{ background: "var(--bg-primary)" }}>{apiKeySourceLabels[m.api_key_source || "none"] || m.api_key_source || "未配置"}</code></p>
                  </div>
                </div>
              ))}

              <div className="p-4 rounded-xl border" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                <h3 className="text-sm font-medium mb-3">添加自定义模型</h3>
                <div className="space-y-2">
                  <select
                    value={newModelProvider}
                    onChange={e => setNewModelProvider(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg border text-sm outline-none"
                    style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                  >
                    {(providers.length ? providers : [{ name: "openai", api_key_env: "OPENAI_API_KEY", base_url: "" }]).map(provider => (
                      <option key={provider.name} value={provider.name}>{provider.name}</option>
                    ))}
                  </select>
                  <input
                    value={newModelName}
                    onChange={e => setNewModelName(e.target.value)}
                    placeholder="模型名称（如 gpt-5.5）"
                    className="w-full px-3 py-2 rounded-lg border text-sm outline-none"
                    style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                  />
                  <input
                    value={newModelDisplay}
                    onChange={e => setNewModelDisplay(e.target.value)}
                    placeholder="显示名称（如 Galaxy gpt-5.5）"
                    className="w-full px-3 py-2 rounded-lg border text-sm outline-none"
                    style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                  />
                  <input
                    value={newModelBaseUrl}
                    onChange={e => setNewModelBaseUrl(e.target.value)}
                    placeholder="接口地址（如 https://gpt.eacase.de5.net/v1）"
                    className="w-full px-3 py-2 rounded-lg border text-sm outline-none"
                    style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                  />
                  <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
                    OpenAI-compatible 网关通常 provider 选 <code>openai</code>，模型名称填网关支持的模型名，接口地址填网关的 <code>/v1</code> 地址。
                  </p>
                  <button
                    onClick={handleAddModel}
                    disabled={!newModelName || !newModelDisplay}
                    className="flex items-center gap-1 px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-30"
                    style={{ background: "var(--accent)", color: "#fff" }}
                  >
                    <Plus className="w-4 h-4" /> 添加模型
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Memory */}
          {tab === "memory" && (
            <div className="max-w-2xl space-y-4">
              <h2 className="text-lg font-semibold">长期记忆</h2>
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                跨对话持久化的记忆存储。
              </p>
              {/* Add */}
              <div className="flex gap-2">
                <input
                  value={newMemKey}
                  onChange={e => setNewMemKey(e.target.value)}
                  placeholder="键"
                  className="flex-1 px-3 py-2 rounded-lg border text-sm outline-none"
                  style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                />
                <input
                  value={newMemVal}
                  onChange={e => setNewMemVal(e.target.value)}
                  placeholder="值"
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
              {memories.length === 0 && <p className="text-sm text-center py-4" style={{ color: "var(--text-secondary)" }}>还没有记忆条目</p>}
            </div>
          )}

          {/* Skills */}
          {tab === "skills" && (
            <div className="max-w-2xl space-y-4">
              <h2 className="text-lg font-semibold">技能</h2>
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                智能体的内置技能与自定义技能。
              </p>
              {skills.map(s => (
                <div key={s.name} className="p-4 rounded-xl border" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-medium text-sm">{s.display_name}</span>
                    <div className="flex items-center gap-2">
                      {s.built_in && <span className="text-xs px-2 py-0.5 rounded-full" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>内置</span>}
                      <span className={`text-xs px-2 py-0.5 rounded-full ${s.enabled ? "bg-green-900 text-green-300" : "bg-red-900 text-red-300"}`}>
                        {s.enabled ? "已启用" : "已禁用"}
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
              <h2 className="text-lg font-semibold">MCP 服务</h2>
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                通过模型上下文协议（MCP）连接外部工具。
              </p>
              <div className="flex gap-2">
                <input
                  value={newMcpName}
                  onChange={e => setNewMcpName(e.target.value)}
                  placeholder="服务名称"
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
              {mcpServers.length === 0 && <p className="text-sm text-center py-4" style={{ color: "var(--text-secondary)" }}>还没有配置 MCP 服务</p>}

              {mcpTools.length > 0 && (
                <>
                  <h3 className="text-sm font-semibold mt-4">可用工具</h3>
                  {mcpTools.map((t, i) => (
                    <div key={i} className="p-3 rounded-lg border text-sm" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                      <span className="font-medium">{t.name}</span>
                      <span className="ml-2 text-xs" style={{ color: "var(--text-secondary)" }}>来自 {t.server}</span>
                      <p className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>{t.description}</p>
                    </div>
                  ))}
                </>
              )}
            </div>
          )}

          {/* Knowledge Base */}
          {tab === "knowledge" && (
            <div className="max-w-2xl space-y-4">
              <h2 className="text-lg font-semibold">知识库</h2>
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                上传文档供 AI 通过 RAG 检索参考。支持 .txt、.md、.csv 等文本文件。
              </p>
              <div className="flex gap-2">
                <label className="flex items-center gap-2 px-4 py-2 rounded-lg cursor-pointer text-sm font-medium" style={{ background: "var(--accent)", color: "#fff" }}>
                  <Upload className="w-4 h-4" /> 上传文档
                  <input type="file" className="hidden" accept=".txt,.md,.csv,.json,.html,.xml,.log,.py,.js,.ts" onChange={handleKbUpload} />
                </label>
              </div>
              {kbDocs.map((d: any) => (
                <div key={d.doc_id} className="p-4 rounded-xl border flex items-start justify-between gap-3" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                  <div>
                    <div className="font-medium text-sm">{d.name}</div>
                    <div className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>
                      {(d.size / 1024).toFixed(1)} KB · {d.chunks} 个分块
                    </div>
                  </div>
                  <button onClick={() => handleKbDelete(d.doc_id)} className="p-1 rounded hover:text-red-400" style={{ color: "var(--text-secondary)" }}>
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ))}
              {kbDocs.length === 0 && <p className="text-sm text-center py-4" style={{ color: "var(--text-secondary)" }}>还没有上传文档</p>}

              <h3 className="text-sm font-semibold mt-4">搜索知识库</h3>
              <div className="flex gap-2">
                <input
                  value={kbSearch}
                  onChange={e => setKbSearch(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && handleKbSearch()}
                  placeholder="输入搜索内容..."
                  className="flex-1 px-3 py-2 rounded-lg border text-sm outline-none"
                  style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                />
                <button onClick={handleKbSearch} className="px-3 py-2 rounded-lg text-sm" style={{ background: "var(--accent)", color: "#fff" }}>搜索</button>
              </div>
              {kbResults.map((r: any, i: number) => (
                <div key={i} className="p-3 rounded-lg border text-sm" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-medium text-xs">{r.doc_name}</span>
                    <span className="text-xs" style={{ color: "var(--text-secondary)" }}>得分：{r.score}</span>
                  </div>
                  <p className="text-xs whitespace-pre-wrap" style={{ color: "var(--text-secondary)" }}>{r.text.slice(0, 300)}{r.text.length > 300 ? "..." : ""}</p>
                </div>
              ))}
            </div>
          )}

          {tab === "demo" && (
            <div className="max-w-2xl space-y-4">
              <h2 className="text-lg font-semibold">演示模式</h2>
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                一键初始化 Memory、Knowledge 和会话轨迹，用于快速展示平台能力。
              </p>
              <div className="p-4 rounded-xl border space-y-4" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                <div className="space-y-3">
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={demoClean}
                      onChange={e => setDemoClean(e.target.checked)}
                    />
                    先清理旧的 Demo 数据，再重新写入
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={demoDryRun}
                      onChange={e => setDemoDryRun(e.target.checked)}
                    />
                    只预览计划，不真正写入
                  </label>
                </div>
                <button
                  onClick={handleSeedDemo}
                  disabled={demoLoading}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
                  style={{ background: "var(--accent)", color: "#fff" }}
                >
                  <Sparkles className="w-4 h-4" />
                  {demoLoading ? "正在处理..." : demoDryRun ? "预览演示数据" : "初始化演示数据"}
                </button>
              </div>

              {demoError && (
                <div className="p-3 rounded-lg border text-sm text-red-300" style={{ borderColor: "rgba(248,113,113,0.35)", background: "rgba(127,29,29,0.25)" }}>
                  {demoError}
                </div>
              )}

              {demoResult && (
                <div className="p-4 rounded-xl border space-y-3" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold">{demoResult.dry_run ? "预览结果" : "初始化结果"}</h3>
                      <p className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>
                        {demoResult.summary.passed}/{demoResult.summary.total} 个动作成功
                      </p>
                    </div>
                    <span className={`text-xs px-2 py-0.5 rounded-full ${demoResult.ok ? "bg-green-900 text-green-300" : "bg-red-900 text-red-300"}`}>
                      {demoResult.ok ? "成功" : "有失败"}
                    </span>
                  </div>
                  <div className="space-y-2">
                    {demoResult.results.map((item, index) => (
                      <div key={`${item.action}-${index}`} className="p-3 rounded-lg border text-xs" style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}>
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-medium">{item.action}</span>
                          <span className={item.ok ? "text-green-400" : "text-red-400"}>{item.ok ? "PASS" : "FAIL"}</span>
                        </div>
                        <div className="mt-1" style={{ color: "var(--text-secondary)" }}>{item.target}</div>
                        <div className="mt-1" style={{ color: "var(--text-secondary)" }}>{item.message}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {tab === "security" && (
            <div className="max-w-5xl space-y-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold">安全策略</h2>
                  <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
                    查看生产安全策略、权限矩阵和最近工具审计记录。
                  </p>
                </div>
                <button
                  onClick={loadSecurity}
                  disabled={securityLoading}
                  className="px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
                  style={{ background: "var(--accent)", color: "#fff" }}
                >
                  {securityLoading ? "刷新中..." : "刷新"}
                </button>
              </div>

              {securityError && (
                <div className="p-3 rounded-lg border text-sm text-red-300" style={{ borderColor: "rgba(248,113,113,0.35)", background: "rgba(127,29,29,0.25)" }}>
                  {securityError}
                </div>
              )}

              {securityPolicy && (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div className="p-4 rounded-xl border" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                    <div className="text-xs" style={{ color: "var(--text-secondary)" }}>运行环境</div>
                    <div className="mt-2 text-lg font-semibold">{securityPolicy.environment}</div>
                  </div>
                  <div className="p-4 rounded-xl border" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                    <div className="text-xs" style={{ color: "var(--text-secondary)" }}>生产模式</div>
                    <div className={`mt-2 text-lg font-semibold ${securityPolicy.production ? "text-red-300" : "text-green-300"}`}>
                      {securityPolicy.production ? "已启用" : "未启用"}
                    </div>
                  </div>
                  <div className="p-4 rounded-xl border" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                    <div className="text-xs" style={{ color: "var(--text-secondary)" }}>禁用工具</div>
                    <div className="mt-2 text-lg font-semibold">{securityPolicy.disabled_tools.length}</div>
                  </div>
                </div>
              )}

              {securityPolicy && (
                <div className="p-4 rounded-xl border space-y-3" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                  <h3 className="text-sm font-semibold">权限矩阵</h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {Object.entries(securityPolicy.matrix).map(([risk, tools]) => (
                      <div key={risk} className="p-3 rounded-lg border" style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}>
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-sm font-medium">{riskLabels[risk] || risk}</span>
                          <span className="text-xs px-2 py-0.5 rounded-full" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>
                            {tools.length}
                          </span>
                        </div>
                        <div className="flex flex-wrap gap-1">
                          {tools.slice(0, 18).map(tool => (
                            <span key={tool} className="text-xs px-2 py-0.5 rounded-full" style={{ background: "var(--bg-secondary)", color: "var(--text-secondary)" }}>
                              {tool}
                            </span>
                          ))}
                          {tools.length > 18 && (
                            <span className="text-xs px-2 py-0.5 rounded-full" style={{ background: "var(--bg-secondary)", color: "var(--text-secondary)" }}>
                              +{tools.length - 18}
                            </span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="p-4 rounded-xl border space-y-3" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold">最近审计记录</h3>
                  <span className="text-xs" style={{ color: "var(--text-secondary)" }}>{securityAudit.length} 条</span>
                </div>
                <div className="space-y-2">
                  {securityAudit.map(event => (
                    <div key={event.event_id} className="p-3 rounded-lg border text-xs" style={{ borderColor: "var(--border-color)", background: "var(--bg-primary)" }}>
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="font-medium truncate">{event.tool}</span>
                          <span className="px-2 py-0.5 rounded-full" style={{ background: "var(--bg-secondary)", color: "var(--text-secondary)" }}>
                            {riskLabels[event.risk_level] || event.risk_level}
                          </span>
                        </div>
                        <span className={`px-2 py-0.5 rounded-full ${decisionClasses[event.decision] || "bg-gray-800 text-gray-300"}`}>
                          {event.decision}
                        </span>
                      </div>
                      <div className="mt-2 grid grid-cols-1 md:grid-cols-3 gap-2" style={{ color: "var(--text-secondary)" }}>
                        <span>{new Date(event.timestamp).toLocaleString()}</span>
                        <span>thread: {event.thread_id || "-"}</span>
                        <span>source: {event.source}</span>
                      </div>
                      <div className="mt-2" style={{ color: "var(--text-secondary)" }}>{event.reason}</div>
                    </div>
                  ))}
                  {securityAudit.length === 0 && (
                    <p className="text-sm text-center py-4" style={{ color: "var(--text-secondary)" }}>暂无审计记录</p>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Tracing */}
          {tab === "tracing" && (
            <div className="max-w-2xl space-y-4">
              <h2 className="text-lg font-semibold">可观测性与追踪</h2>
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                监控大模型调用和智能体执行轨迹。
              </p>
              {["langsmith", "langfuse"].map(p => {
                const info = tracing[p] || {};
                return (
                  <div key={p} className="p-4 rounded-xl border" style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}>
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-sm capitalize">{p}</span>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${info.enabled ? "bg-green-900 text-green-300" : "bg-gray-700 text-gray-400"}`}>
                        {info.enabled ? "已启用" : "未启用"}
                      </span>
                    </div>
                    <p className="text-xs mt-2" style={{ color: "var(--text-secondary)" }}>
                      {info.enabled
                        ? p === "langsmith"
                          ? `项目：${info.project || "default"}`
                          : "公钥已配置"
                        : `请设置 ${p.toUpperCase()}_* 环境变量并重启后端`}
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
