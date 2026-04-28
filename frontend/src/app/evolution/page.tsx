"use client";

import { useState, useEffect, useCallback } from "react";
import {
  fetchSkillsList,
  fetchSkillLibrary,
  installSkillFromLibrary,
  previewOpenClawSkill,
  installOpenClawSkill,
  searchClawHubSkills,
  fetchClawHubAuthStatus,
  previewClawHubSkill,
  installClawHubSkill,
  fetchSkillDetail,
  fetchSkillVersions,
  rollbackSkill,
  copySkill,
  evolveSkill,
  applyEvolutionCandidate,
  fetchEvolutionLog,
  fetchEvolutionStats,
  fetchCustomTools,
} from "@/lib/api";
import {
  ArrowLeft,
  Dna,
  Wrench,
  BookOpen,
  Clock,
  RotateCcw,
  ChevronRight,
  Activity,
  Layers,
  Download,
} from "lucide-react";

interface Skill {
  name: string;
  display_name: string;
  description: string;
  category?: string;
}

interface LibrarySkill extends Skill {
  tags?: string[];
  tools?: string[];
  installed?: boolean;
  source?: string;
}

interface OpenClawPreview {
  ok?: boolean;
  message?: string;
  skill?: LibrarySkill & {
    source_url?: string;
    license?: string;
    homepage?: string;
    repository?: string;
    required_environment_variables?: { name: string; required?: boolean }[];
    required_binaries?: string[];
  };
  system_prompt_preview?: string;
  system_prompt_length?: number;
  security?: { safe?: boolean; threats?: string[] };
}

interface ClawHubResult {
  slug: string;
  displayName?: string;
  summary?: string;
  score?: number;
  version?: string | null;
  updatedAt?: number;
  installed?: boolean;
}

interface ClawHubAuthStatus {
  authenticated?: boolean;
  handle?: string | null;
  message?: string;
}

interface SkillDetail {
  name: string;
  display_name: string;
  description: string;
  system_prompt: string;
  version: number;
  tools: string[];
  enabled: boolean;
  built_in?: boolean;
  editable?: boolean;
}

interface EvolutionEvent {
  action?: string;
  name?: string;
  description?: string;
  timestamp?: string;
  run_id?: string;
  target_name?: string;
  target_type?: string;
  status?: string;
  improvement?: number;
  started_at?: string;
  finished_at?: string;
}

interface EvolutionStats {
  total_runs?: number;
  total_traces?: number;
  skill_stats?: Record<string, { success?: number; fail?: number; total_traces?: number; success_rate?: number }>;
  pending_triage?: unknown[];
}

type Tab = "skills" | "library" | "tools" | "log" | "stats";

const CLAWHUB_SUGGESTED_QUERIES = ["calendar", "wechat", "browser", "github", "pdf", "notion", "email", "slack"];

export default function EvolutionPage() {
  const [tab, setTab] = useState<Tab>("skills");
  const [skills, setSkills] = useState<Skill[]>([]);
  const [librarySkills, setLibrarySkills] = useState<LibrarySkill[]>([]);
  const [tools, setTools] = useState<any[]>([]);
  const [log, setLog] = useState<EvolutionEvent[]>([]);
  const [stats, setStats] = useState<EvolutionStats | null>(null);
  const [selectedSkill, setSelectedSkill] = useState<SkillDetail | null>(null);
  const [versions, setVersions] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [actionMessage, setActionMessage] = useState("");
  const [libraryMessage, setLibraryMessage] = useState("");
  const [openClawUrl, setOpenClawUrl] = useState("");
  const [openClawContent, setOpenClawContent] = useState("");
  const [openClawPreview, setOpenClawPreview] = useState<OpenClawPreview | null>(null);
  const [clawHubQuery, setClawHubQuery] = useState("calendar");
  const [clawHubResults, setClawHubResults] = useState<ClawHubResult[]>([]);
  const [clawHubPreview, setClawHubPreview] = useState<OpenClawPreview | null>(null);
  const [clawHubSelectedSlug, setClawHubSelectedSlug] = useState("");
  const [clawHubAuthStatus, setClawHubAuthStatus] = useState<ClawHubAuthStatus | null>(null);
  const [clawHubSearched, setClawHubSearched] = useState(false);
  const [clawHubResultQuery, setClawHubResultQuery] = useState("");
  const [canApplyCandidate, setCanApplyCandidate] = useState(false);

  const loadSkills = useCallback(async () => {
    try {
      const data = await fetchSkillsList();
      setSkills(Array.isArray(data) ? data : data.skills || []);
    } catch { setSkills([]); }
  }, []);

  const loadTools = useCallback(async () => {
    try {
      const data = await fetchCustomTools();
      setTools(data.tools || []);
    } catch { setTools([]); }
  }, []);

  const loadSkillLibrary = useCallback(async () => {
    try {
      const data = await fetchSkillLibrary();
      setLibrarySkills(Array.isArray(data.skills) ? data.skills : []);
    } catch { setLibrarySkills([]); }
  }, []);

  const loadClawHubAuthStatus = useCallback(async () => {
    try {
      setClawHubAuthStatus(await fetchClawHubAuthStatus());
    } catch { setClawHubAuthStatus(null); }
  }, []);

  const loadLog = useCallback(async () => {
    try {
      const data = await fetchEvolutionLog();
      setLog(data.log || data.history || []);
    } catch { setLog([]); }
  }, []);

  const loadStats = useCallback(async () => {
    try {
      const data = await fetchEvolutionStats();
      setStats(data);
    } catch { setStats(null); }
  }, []);

  useEffect(() => {
    loadSkills();
    loadSkillLibrary();
    loadClawHubAuthStatus();
    loadTools();
    loadLog();
    loadStats();
  }, [loadSkills, loadSkillLibrary, loadClawHubAuthStatus, loadTools, loadLog, loadStats]);

  const handleInstallLibrarySkill = async (name: string) => {
    setLoading(true);
    setLibraryMessage("");
    try {
      const result = await installSkillFromLibrary(name);
      setLibraryMessage(result?.message || (result?.ok ? "安装成功" : "安装失败"));
      await loadSkills();
      await loadSkillLibrary();
    } finally {
      setLoading(false);
    }
  };

  const handlePreviewOpenClaw = async () => {
    setLoading(true);
    setLibraryMessage("");
    setOpenClawPreview(null);
    try {
      const result = await previewOpenClawSkill({ content: openClawContent.trim(), url: openClawUrl.trim() });
      setOpenClawPreview(result);
      if (!result?.ok) setLibraryMessage(result?.message || "预览失败");
    } finally {
      setLoading(false);
    }
  };

  const handleInstallOpenClaw = async (force = false) => {
    setLoading(true);
    setLibraryMessage("");
    try {
      const result = await installOpenClawSkill({ content: openClawContent.trim(), url: openClawUrl.trim(), force });
      setLibraryMessage(result?.message || (result?.ok ? "安装成功" : "安装失败"));
      await loadSkills();
      await loadSkillLibrary();
    } finally {
      setLoading(false);
    }
  };

  const handleSearchClawHub = useCallback(async (queryOverride?: string) => {
    const query = (queryOverride ?? clawHubQuery).trim();
    if (!query) return;
    if (queryOverride !== undefined) setClawHubQuery(query);
    setLoading(true);
    setLibraryMessage("");
    setClawHubPreview(null);
    setClawHubSearched(true);
    setClawHubResultQuery(query);
    try {
      const result = await searchClawHubSkills(query, 50);
      setClawHubResults(Array.isArray(result.results) ? result.results : []);
      if (result.error) setLibraryMessage(result.error);
    } finally {
      setLoading(false);
    }
  }, [clawHubQuery]);

  const handlePreviewClawHub = async (slug: string) => {
    setLoading(true);
    setLibraryMessage("");
    setClawHubSelectedSlug(slug);
    setClawHubPreview(null);
    try {
      const result = await previewClawHubSkill(slug);
      setClawHubPreview(result);
      if (!result?.ok) setLibraryMessage(result?.message || "ClawHub 预览失败");
    } finally {
      setLoading(false);
    }
  };

  const handleInstallClawHub = async (slug: string, force = false) => {
    setLoading(true);
    setLibraryMessage("");
    try {
      const result = await installClawHubSkill(slug, { force });
      setLibraryMessage(result?.message || (result?.ok ? "安装成功" : "安装失败"));
      await loadSkills();
      await handleSearchClawHub();
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (tab === "library" && !clawHubSearched) {
      void handleSearchClawHub(clawHubQuery);
    }
  }, [tab, clawHubSearched, clawHubQuery, handleSearchClawHub]);

  const viewSkill = async (name: string) => {
    setLoading(true);
    try {
      const [detail, vers] = await Promise.all([
        fetchSkillDetail(name),
        fetchSkillVersions(name),
      ]);
      setCanApplyCandidate(false);
      if (detail?.error) {
        setSelectedSkill(null);
      } else {
        setSelectedSkill({ ...detail, tools: Array.isArray(detail.tools) ? detail.tools : [] });
      }
      setVersions(vers.versions || []);
    } catch {}
    setLoading(false);
  };

  const handleRollback = async (name: string) => {
    await rollbackSkill(name);
    viewSkill(name);
    loadLog();
  };

  const handleCopySkill = async () => {
    if (!selectedSkill) return;
    setLoading(true);
    setActionMessage("");
    try {
      const result = await copySkill(selectedSkill.name);
      if (result?.name) {
        setActionMessage(`已复制为自定义技能：${result.name}`);
        await loadSkills();
        await viewSkill(result.name);
      } else {
        setActionMessage(result?.error || result?.message || "复制失败");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleEvolveSkill = async () => {
    if (!selectedSkill) return;
    setLoading(true);
    setActionMessage("");
    try {
      const result = await evolveSkill(selectedSkill.name, selectedSkill.system_prompt, 2);
      if (result?.run_id) {
        setActionMessage(`已完成一次进化评估：${result.run_id}，提升 ${result.improvement ?? 0}`);
        setCanApplyCandidate((result.improvement ?? 0) > 0);
        await loadLog();
        await loadStats();
      } else {
        setActionMessage(result?.error || "进化失败");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleApplyCandidate = async () => {
    if (!selectedSkill) return;
    setLoading(true);
    setActionMessage("");
    try {
      const result = await applyEvolutionCandidate(selectedSkill.name);
      if (result?.ok) {
        setActionMessage("已应用最佳进化候选到当前技能。");
        setCanApplyCandidate(false);
        await viewSkill(selectedSkill.name);
        await loadLog();
      } else {
        setActionMessage(result?.error || result?.message || "应用失败");
      }
    } finally {
      setLoading(false);
    }
  };

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "skills", label: "技能", icon: <BookOpen size={16} /> },
    { id: "library", label: "技能库", icon: <Download size={16} /> },
    { id: "tools", label: "工具", icon: <Wrench size={16} /> },
    { id: "log", label: "进化记录", icon: <Clock size={16} /> },
    { id: "stats", label: "统计", icon: <Activity size={16} /> },
  ];

  return (
    <div className="h-screen bg-gray-950 text-gray-100 flex flex-col overflow-hidden">
      {/* Header */}
      <header className="border-b border-gray-800 bg-gray-900/50 backdrop-blur-sm sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center gap-4">
          <a href="/" className="text-gray-400 hover:text-gray-200 transition-colors">
            <ArrowLeft size={20} />
          </a>
          <Dna size={24} className="text-purple-400" />
          <div>
            <h1 className="text-xl font-semibold">自进化中心</h1>
            <p className="text-sm text-gray-400">技能、工具、进化记录和运行统计</p>
          </div>
        </div>
      </header>

      {/* Tabs */}
      <div className="max-w-6xl mx-auto px-6 pt-4 w-full flex-shrink-0">
        <div className="flex gap-1 bg-gray-900 rounded-lg p-1">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => { setTab(t.id); setSelectedSkill(null); }}
              className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                tab === t.id
                  ? "bg-purple-600 text-white"
                  : "text-gray-400 hover:text-gray-200 hover:bg-gray-800"
              }`}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <main className="max-w-6xl mx-auto px-6 py-6 w-full flex-1 overflow-y-auto">
        {/* Skills Tab */}
        {tab === "skills" && !selectedSkill && (
          <div className="space-y-3">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Layers size={20} className="text-purple-400" />
              技能列表（{skills.length}）
            </h2>
            {skills.length === 0 ? (
              <div className="text-gray-500 bg-gray-900 rounded-lg p-8 text-center">
                暂无自定义技能。当前系统不会凭空创建技能；当你让 AI 多次完成同类复杂任务，或手动创建技能后，会显示在这里。
              </div>
            ) : (
              <div className="grid gap-3">
                {skills.map((s) => (
                  <button
                    key={s.name}
                    onClick={() => viewSkill(s.name)}
                    className="bg-gray-900 border border-gray-800 rounded-lg p-4 text-left hover:border-purple-600/50 transition-colors group"
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <h3 className="font-medium text-gray-200 group-hover:text-purple-300">
                          {s.display_name}
                        </h3>
                        <p className="text-sm text-gray-400 mt-1 line-clamp-2">
                          {s.description}
                        </p>
                      </div>
                      <ChevronRight size={16} className="text-gray-600 group-hover:text-purple-400" />
                    </div>
                    {s.category && (
                      <span className="inline-block mt-2 px-2 py-0.5 bg-gray-800 text-xs text-gray-400 rounded">
                        {s.category}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Skill Detail */}
        {tab === "skills" && selectedSkill && (
          <div className="space-y-4">
            <button
              onClick={() => setSelectedSkill(null)}
              className="text-sm text-gray-400 hover:text-gray-200 flex items-center gap-1"
            >
              <ArrowLeft size={14} /> 返回技能列表
            </button>
            <div className="bg-gray-900 border border-gray-800 rounded-lg p-6">
              <div className="flex items-start justify-between mb-4">
                <div>
                  <h2 className="text-xl font-semibold text-gray-200">
                    {selectedSkill.display_name}
                  </h2>
                  <p className="text-sm text-gray-400 mt-1">{selectedSkill.description}</p>
                  <div className="flex gap-3 mt-2 text-xs text-gray-500">
                    <span>v{selectedSkill.version}</span>
                    <span>{selectedSkill.enabled ? "✅ 已启用" : "❌ 已禁用"}</span>
                    <span>{selectedSkill.built_in ? "内置技能（只读）" : "自定义技能（可进化）"}</span>
                    {selectedSkill.tools.length > 0 && (
                      <span>🔧 {selectedSkill.tools.join(", ")}</span>
                    )}
                  </div>
                </div>
                <div className="flex gap-2">
                  {selectedSkill.built_in ? (
                    <button
                      disabled={loading}
                      onClick={handleCopySkill}
                      className="px-3 py-1.5 bg-purple-600/20 text-purple-300 rounded-md text-sm hover:bg-purple-600/30 transition-colors disabled:opacity-50"
                    >
                      复制为可进化技能
                    </button>
                  ) : (
                    <>
                      <button
                        disabled={loading}
                        onClick={handleEvolveSkill}
                        className="px-3 py-1.5 bg-green-600/20 text-green-300 rounded-md text-sm hover:bg-green-600/30 transition-colors disabled:opacity-50"
                      >
                        运行一次进化
                      </button>
                      {canApplyCandidate && (
                        <button
                          disabled={loading}
                          onClick={handleApplyCandidate}
                          className="px-3 py-1.5 bg-blue-600/20 text-blue-300 rounded-md text-sm hover:bg-blue-600/30 transition-colors disabled:opacity-50"
                        >
                          应用最佳候选
                        </button>
                      )}
                    </>
                  )}
                  {versions.length > 0 && selectedSkill.editable !== false && (
                    <button
                      onClick={() => handleRollback(selectedSkill.name)}
                      className="flex items-center gap-1 px-3 py-1.5 bg-orange-600/20 text-orange-400 rounded-md text-sm hover:bg-orange-600/30 transition-colors"
                    >
                      <RotateCcw size={14} /> 回滚
                    </button>
                  )}
                </div>
              </div>
              {actionMessage && (
                <div className="mb-4 rounded-lg border border-blue-500/20 bg-blue-500/10 p-3 text-sm text-blue-200">
                  {actionMessage}
                </div>
              )}
              <div className="mt-4">
                <h3 className="text-sm font-medium text-gray-400 mb-2">系统提示词</h3>
                <pre className="bg-gray-950 border border-gray-800 rounded-lg p-4 text-sm text-gray-300 whitespace-pre-wrap max-h-96 overflow-y-auto">
                  {selectedSkill.system_prompt}
                </pre>
              </div>
              {selectedSkill.built_in && (
                <div className="mt-4 rounded-lg border border-purple-500/20 bg-purple-500/10 p-3 text-sm text-purple-200">
                  这是系统内置技能，用来展示和调用，不能直接回滚或改写。若要进化，请先创建一个自定义技能或把它复制成自定义技能。
                </div>
              )}
              {versions.length > 0 && (
                <div className="mt-4">
                  <h3 className="text-sm font-medium text-gray-400 mb-2">
                    版本历史（{versions.length}）
                  </h3>
                  <div className="flex flex-wrap gap-2">
                    {versions.map((v) => (
                      <span
                        key={v}
                        className="px-2 py-1 bg-gray-800 text-xs text-gray-400 rounded"
                      >
                        {v}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Skill Library Tab */}
        {tab === "library" && (
          <div className="space-y-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-lg font-semibold flex items-center gap-2">
                  <Download size={20} className="text-purple-400" />
                  生态技能库（{librarySkills.length}）
                </h2>
                <p className="text-sm text-gray-400 mt-1">
                  类似 OpenClaw 的技能生态：按场景安装技能，安装后会出现在当前系统技能列表里。
                </p>
              </div>
              <button
                disabled={loading}
                onClick={loadSkillLibrary}
                className="px-3 py-1.5 rounded-md text-sm bg-gray-800 text-gray-300 hover:bg-gray-700 disabled:opacity-50"
              >
                刷新
              </button>
            </div>
            {libraryMessage && (
              <div className="rounded-lg border border-blue-500/20 bg-blue-500/10 p-3 text-sm text-blue-200">
                {libraryMessage}
              </div>
            )}
            <div className="bg-gray-900 border border-blue-900/40 rounded-lg p-4 space-y-3">
              <div>
                <h3 className="font-medium text-gray-100">ClawHub 在线商店</h3>
                <p className="text-sm text-gray-400 mt-1">
                  这里搜索的是 ClawHub 真实商店，不是下面的本地精选列表。ClawHub 技能很多，不会一次性全部加载，请输入关键词搜索。
                </p>
              </div>
              {clawHubAuthStatus && (
                <div className={`rounded-lg border p-3 text-sm ${clawHubAuthStatus.authenticated ? "border-green-500/20 bg-green-500/10 text-green-200" : "border-yellow-500/20 bg-yellow-500/10 text-yellow-200"}`}>
                  {clawHubAuthStatus.message}
                  {!clawHubAuthStatus.authenticated && (
                    <span className="block mt-1 text-xs">
                      解除下载限流：设置环境变量 CLAWHUB_TOKEN 后重启后端，或使用 ClawHub CLI 登录生成配置。
                    </span>
                  )}
                </div>
              )}
              <div className="flex gap-2">
                <input
                  value={clawHubQuery}
                  onChange={(e) => setClawHubQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleSearchClawHub();
                  }}
                  placeholder="搜索 ClawHub 技能，例如 calendar、wechat、browser"
                  className="flex-1 px-3 py-2 rounded-lg bg-gray-950 border border-gray-800 text-sm text-gray-200 outline-none focus:border-blue-600"
                />
                <button
                  disabled={loading || !clawHubQuery.trim()}
                  onClick={() => handleSearchClawHub()}
                  className="px-4 py-2 rounded-lg text-sm bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-50"
                >
                  搜索
                </button>
              </div>
              <div className="flex flex-wrap gap-2">
                {CLAWHUB_SUGGESTED_QUERIES.map((query) => (
                  <button
                    key={query}
                    disabled={loading}
                    onClick={() => handleSearchClawHub(query)}
                    className="px-2.5 py-1 rounded-full text-xs bg-gray-800 text-gray-300 hover:bg-blue-900/40 hover:text-blue-200 disabled:opacity-50"
                  >
                    {query}
                  </button>
                ))}
              </div>
              <div className="flex items-center justify-between text-xs text-gray-500">
                <span>
                  {clawHubSearched
                    ? `ClawHub 搜索结果：${clawHubResults.length} 个（关键词：${clawHubResultQuery}）`
                    : "进入技能库会自动搜索 calendar；也可以点上面的关键词。"}
                </span>
                <span>搜索结果来自 ClawHub，安装后才会进入本系统技能列表。</span>
              </div>
              {clawHubSearched && !loading && clawHubResults.length === 0 && (
                <div className="rounded-lg border border-yellow-500/20 bg-yellow-500/10 p-3 text-sm text-yellow-200">
                  没搜到 ClawHub 技能。换个关键词试试，例如 browser、github、pdf、notion。
                </div>
              )}
              {clawHubResults.length > 0 && (
                <div className="grid gap-2">
                  {clawHubResults.map((item) => (
                    <div key={item.slug} className="rounded-lg border border-gray-800 bg-gray-950 p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <h4 className="font-medium text-gray-100 truncate">{item.displayName || item.slug}</h4>
                            {item.installed && <span className="text-xs px-2 py-0.5 rounded-full bg-green-900/50 text-green-300">已安装</span>}
                          </div>
                          <p className="text-sm text-gray-400 mt-1 line-clamp-2">{item.summary || "暂无简介"}</p>
                          <div className="text-xs text-gray-600 mt-1">slug：{item.slug}</div>
                        </div>
                        <div className="flex gap-2 flex-shrink-0">
                          <button
                            disabled={loading}
                            onClick={() => handlePreviewClawHub(item.slug)}
                            className="px-3 py-1.5 rounded-md text-sm bg-gray-800 text-gray-200 hover:bg-gray-700 disabled:opacity-50"
                          >
                            预览
                          </button>
                          <button
                            disabled={loading || item.installed}
                            onClick={() => handleInstallClawHub(item.slug)}
                            className="px-3 py-1.5 rounded-md text-sm bg-blue-600 text-white hover:bg-blue-500 disabled:bg-gray-800 disabled:text-gray-500"
                          >
                            {item.installed ? "已安装" : "安装"}
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {clawHubPreview?.skill && (
                <div className="rounded-lg border border-gray-800 bg-gray-950 p-3 space-y-2">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-medium text-gray-100">{clawHubPreview.skill.display_name}</div>
                      <div className="text-sm text-gray-400">{clawHubPreview.skill.description}</div>
                    </div>
                    <span className={`text-xs px-2 py-0.5 rounded-full ${clawHubPreview.security?.safe ? "bg-green-900/50 text-green-300" : "bg-red-900/50 text-red-300"}`}>
                      {clawHubPreview.security?.safe ? "安全扫描通过" : "需要人工复核"}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-2 text-xs">
                    <span className="px-2 py-0.5 rounded bg-gray-800 text-gray-400">ClawHub：{clawHubSelectedSlug}</span>
                    <span className="px-2 py-0.5 rounded bg-gray-800 text-gray-400">ID：{clawHubPreview.skill.name}</span>
                    {(clawHubPreview.skill.tools || []).length > 0 && <span className="px-2 py-0.5 rounded bg-blue-900/30 text-blue-300">工具：{clawHubPreview.skill.tools?.join(", ")}</span>}
                    {(clawHubPreview.skill.required_environment_variables || []).length > 0 && <span className="px-2 py-0.5 rounded bg-yellow-900/30 text-yellow-300">需要环境变量：{clawHubPreview.skill.required_environment_variables?.map((v) => v.name).join(", ")}</span>}
                    {(clawHubPreview.skill.required_binaries || []).length > 0 && <span className="px-2 py-0.5 rounded bg-yellow-900/30 text-yellow-300">需要命令：{clawHubPreview.skill.required_binaries?.join(", ")}</span>}
                  </div>
                  {clawHubPreview.security?.threats && clawHubPreview.security.threats.length > 0 && (
                    <div className="text-xs text-red-300">
                      风险：{clawHubPreview.security.threats.join("；")}
                    </div>
                  )}
                  {clawHubPreview.system_prompt_preview && (
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/30 p-2 text-xs text-gray-400">
                      {clawHubPreview.system_prompt_preview}
                    </pre>
                  )}
                  <div className="flex gap-2">
                    <button
                      disabled={loading}
                      onClick={() => handleInstallClawHub(clawHubSelectedSlug)}
                      className="px-3 py-1.5 rounded-md text-sm bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-50"
                    >
                      安装这个 ClawHub 技能
                    </button>
                    {clawHubPreview.security?.safe === false && (
                      <button
                        disabled={loading}
                        onClick={() => handleInstallClawHub(clawHubSelectedSlug, true)}
                        className="px-3 py-1.5 rounded-md text-sm bg-red-600 text-white hover:bg-red-500 disabled:opacity-50"
                      >
                        强制安装
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
            <div className="bg-gray-900 border border-purple-900/40 rounded-lg p-4 space-y-3">
              <div>
                <h3 className="font-medium text-gray-100">导入 OpenClaw / ClawHub 技能</h3>
                <p className="text-sm text-gray-400 mt-1">
                  支持粘贴 OpenClaw `SKILL.md`，或填写 GitHub raw / HTTPS SKILL.md 地址。安装前会先解析并做安全扫描。
                </p>
              </div>
              <input
                value={openClawUrl}
                onChange={(e) => setOpenClawUrl(e.target.value)}
                placeholder="可选：SKILL.md raw URL，例如 https://raw.githubusercontent.com/.../SKILL.md"
                className="w-full px-3 py-2 rounded-lg bg-gray-950 border border-gray-800 text-sm text-gray-200 outline-none focus:border-purple-600"
              />
              <textarea
                value={openClawContent}
                onChange={(e) => setOpenClawContent(e.target.value)}
                placeholder="或直接粘贴 SKILL.md 内容..."
                className="w-full min-h-32 px-3 py-2 rounded-lg bg-gray-950 border border-gray-800 text-sm text-gray-200 outline-none focus:border-purple-600"
              />
              <div className="flex flex-wrap gap-2">
                <button
                  disabled={loading || (!openClawUrl.trim() && !openClawContent.trim())}
                  onClick={handlePreviewOpenClaw}
                  className="px-3 py-1.5 rounded-md text-sm bg-gray-800 text-gray-200 hover:bg-gray-700 disabled:opacity-50"
                >
                  预览与扫描
                </button>
                {openClawPreview?.ok && (
                  <button
                    disabled={loading}
                    onClick={() => handleInstallOpenClaw(false)}
                    className="px-3 py-1.5 rounded-md text-sm bg-purple-600 text-white hover:bg-purple-500 disabled:opacity-50"
                  >
                    安装 OpenClaw 技能
                  </button>
                )}
                {openClawPreview?.ok && openClawPreview.security?.safe === false && (
                  <button
                    disabled={loading}
                    onClick={() => handleInstallOpenClaw(true)}
                    className="px-3 py-1.5 rounded-md text-sm bg-red-600 text-white hover:bg-red-500 disabled:opacity-50"
                  >
                    强制安装
                  </button>
                )}
              </div>
              {openClawPreview?.skill && (
                <div className="rounded-lg border border-gray-800 bg-gray-950 p-3 space-y-2">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-medium text-gray-100">{openClawPreview.skill.display_name}</div>
                      <div className="text-sm text-gray-400">{openClawPreview.skill.description}</div>
                    </div>
                    <span className={`text-xs px-2 py-0.5 rounded-full ${openClawPreview.security?.safe ? "bg-green-900/50 text-green-300" : "bg-red-900/50 text-red-300"}`}>
                      {openClawPreview.security?.safe ? "安全扫描通过" : "需要人工复核"}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-2 text-xs">
                    <span className="px-2 py-0.5 rounded bg-gray-800 text-gray-400">ID：{openClawPreview.skill.name}</span>
                    <span className="px-2 py-0.5 rounded bg-gray-800 text-gray-400">分类：{skillCategoryLabel(openClawPreview.skill.category || "openclaw")}</span>
                    {(openClawPreview.skill.tools || []).length > 0 && <span className="px-2 py-0.5 rounded bg-blue-900/30 text-blue-300">工具：{openClawPreview.skill.tools?.join(", ")}</span>}
                    {(openClawPreview.skill.required_environment_variables || []).length > 0 && <span className="px-2 py-0.5 rounded bg-yellow-900/30 text-yellow-300">需要环境变量：{openClawPreview.skill.required_environment_variables?.map((v) => v.name).join(", ")}</span>}
                    {(openClawPreview.skill.required_binaries || []).length > 0 && <span className="px-2 py-0.5 rounded bg-yellow-900/30 text-yellow-300">需要命令：{openClawPreview.skill.required_binaries?.join(", ")}</span>}
                  </div>
                  {openClawPreview.security?.threats && openClawPreview.security.threats.length > 0 && (
                    <div className="text-xs text-red-300">
                      风险：{openClawPreview.security.threats.join("；")}
                    </div>
                  )}
                  {openClawPreview.system_prompt_preview && (
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/30 p-2 text-xs text-gray-400">
                      {openClawPreview.system_prompt_preview}
                    </pre>
                  )}
                </div>
              )}
            </div>
            <div className="pt-2">
              <h3 className="font-medium text-gray-100">本地精选生态技能（{librarySkills.length}）</h3>
              <p className="text-sm text-gray-500 mt-1">
                下面这些是系统内置精选，不代表 ClawHub 全量。要找 ClawHub 的几万个技能，请用上面的 ClawHub 搜索框。
              </p>
            </div>
            {librarySkills.length === 0 ? (
              <div className="text-gray-500 bg-gray-900 rounded-lg p-8 text-center">
                暂无生态技能数据。请确认后端技能库接口已启动。
              </div>
            ) : (
              <div className="grid gap-3 md:grid-cols-2">
                {librarySkills.map((s) => (
                  <div
                    key={s.name}
                    className="bg-gray-900 border border-gray-800 rounded-lg p-4 flex flex-col gap-3"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h3 className="font-medium text-gray-100">{s.display_name}</h3>
                        <p className="text-sm text-gray-400 mt-1 line-clamp-2">{s.description}</p>
                      </div>
                      <span className={`text-xs px-2 py-0.5 rounded-full whitespace-nowrap ${s.installed ? "bg-green-900/50 text-green-300" : "bg-purple-900/50 text-purple-300"}`}>
                        {s.installed ? "已安装" : "可安装"}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {s.category && <span className="px-2 py-0.5 bg-gray-800 text-xs text-gray-400 rounded">{skillCategoryLabel(s.category)}</span>}
                      {(s.tags || []).slice(0, 4).map((tag) => (
                        <span key={tag} className="px-2 py-0.5 bg-gray-800/70 text-xs text-gray-500 rounded">{tag}</span>
                      ))}
                      {(s.tools || []).length > 0 && (
                        <span className="px-2 py-0.5 bg-blue-900/30 text-xs text-blue-300 rounded">
                          工具 {s.tools?.length}
                        </span>
                      )}
                    </div>
                    <div className="mt-auto flex items-center justify-between gap-3">
                      <span className="text-xs text-gray-600">ID：{s.name}</span>
                      <button
                        disabled={loading || s.installed}
                        onClick={() => handleInstallLibrarySkill(s.name)}
                        className="px-3 py-1.5 rounded-md text-sm bg-purple-600 text-white hover:bg-purple-500 disabled:bg-gray-800 disabled:text-gray-500"
                      >
                        {s.installed ? "已安装" : "安装技能"}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Tools Tab */}
        {tab === "tools" && (
          <div className="space-y-3">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Wrench size={20} className="text-blue-400" />
              可用工具（{tools.length}）
            </h2>
            {tools.length === 0 ? (
              <div className="text-gray-500 bg-gray-900 rounded-lg p-8 text-center">
                暂无工具数据。工具列表来自后端工具注册表；如果这里为空，说明工具注册接口没有返回。
              </div>
            ) : (
              <div className="grid gap-3">
                {tools.map((t: any, i: number) => (
                  <div
                    key={i}
                    className="bg-gray-900 border border-gray-800 rounded-lg p-4"
                  >
                    <div className="flex items-center gap-2">
                      <h3 className="font-medium text-gray-200">{t.name || t.display_name}</h3>
                      <span className={`text-xs px-2 py-0.5 rounded-full ${t.built_in ? "bg-gray-800 text-gray-400" : "bg-purple-900/50 text-purple-300"}`}>
                        {t.built_in ? "内置工具" : "自定义工具"}
                      </span>
                    </div>
                    <p className="text-sm text-gray-400 mt-1">{t.description}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Log Tab */}
        {tab === "log" && (
          <div className="space-y-3">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Clock size={20} className="text-green-400" />
              进化记录（{log.length} 条）
            </h2>
            {log.length === 0 ? (
              <div className="text-gray-500 bg-gray-900 rounded-lg p-8 text-center">
                暂无进化记录。只有调用“进化技能”或发生技能编辑/回滚后，这里才会产生记录。
              </div>
            ) : (
              <div className="space-y-2">
                {log.slice().reverse().map((e, i) => {
                  const action = e.action || e.status || "记录";
                  const name = e.name || e.target_name || e.run_id || "未知对象";
                  const description = e.description || (e.target_type ? `类型：${targetTypeLabel(e.target_type)}` : "");
                  const timestamp = e.timestamp || e.finished_at || e.started_at || "";
                  return (
                  <div
                    key={i}
                    className="bg-gray-900 border border-gray-800 rounded-lg px-4 py-3 flex items-center gap-3"
                  >
                    <span className={`text-xs px-2 py-0.5 rounded ${evolutionActionClass(action)}`}>
                      {evolutionActionLabel(action)}
                    </span>
                    <span className="font-medium text-gray-300">{name}</span>
                    {description && (
                      <span className="text-sm text-gray-500 truncate">{description}</span>
                    )}
                    {typeof e.improvement === "number" && <span className="text-xs text-purple-300">提升 {e.improvement}</span>}
                    <span className="ml-auto text-xs text-gray-600 whitespace-nowrap">
                      {timestamp?.slice(0, 16).replace("T", " ")}
                    </span>
                  </div>
                );})}
              </div>
            )}
          </div>
        )}

        {/* Stats Tab */}
        {tab === "stats" && (
          <div className="space-y-4">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Activity size={20} className="text-yellow-400" />
              进化统计
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
                <div className="text-3xl font-bold text-purple-400">{skills.length}</div>
                <div className="text-sm text-gray-400 mt-1">技能数量</div>
              </div>
              <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
                <div className="text-3xl font-bold text-blue-400">{tools.length}</div>
                <div className="text-sm text-gray-400 mt-1">工具数量</div>
              </div>
              <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
                <div className="text-3xl font-bold text-green-400">{log.length}</div>
                <div className="text-sm text-gray-400 mt-1">进化记录</div>
              </div>
            </div>
            {stats && stats.skill_stats && Object.keys(stats.skill_stats).length > 0 ? (
              <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
                <h3 className="text-sm font-medium text-gray-400 mb-3">技能表现</h3>
                <div className="space-y-2">
                  {Object.entries(stats.skill_stats).map(([name, s]) => {
                    const total = (s.success || 0) + (s.fail || 0) || s.total_traces || 0;
                    const rate = typeof s.success_rate === "number" ? Math.round(s.success_rate * 100) : total > 0 ? Math.round(((s.success || 0) / total) * 100) : 0;
                    return (
                      <div key={name} className="flex items-center gap-3">
                        <span className="text-sm text-gray-300 w-40 truncate">{name}</span>
                        <div className="flex-1 bg-gray-800 rounded-full h-2 overflow-hidden">
                          <div
                            className="bg-green-500 h-full rounded-full"
                            style={{ width: `${rate}%` }}
                          />
                        </div>
                        <span className="text-xs text-gray-500 w-20 text-right">
                          {rate}%（{s.success || 0}/{total}）
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              <div className="text-gray-500 bg-gray-900 rounded-lg p-8 text-center">
                暂无技能表现数据。运行带有成功/失败反馈的技能后，这里会显示成功率。
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

function evolutionActionLabel(action: string): string {
  if (action.includes("completed")) return "已完成";
  if (action.includes("failed")) return "失败";
  if (action.includes("create")) return "创建";
  if (action.includes("patch")) return "补丁";
  if (action.includes("edit")) return "编辑";
  if (action.includes("rollback")) return "回滚";
  if (action.includes("remove")) return "删除";
  if (action.includes("running")) return "运行中";
  return action || "记录";
}

function evolutionActionClass(action: string): string {
  if (action.includes("create") || action.includes("completed")) return "bg-green-900/50 text-green-400";
  if (action.includes("patch")) return "bg-blue-900/50 text-blue-400";
  if (action.includes("edit")) return "bg-yellow-900/50 text-yellow-400";
  if (action.includes("rollback")) return "bg-orange-900/50 text-orange-400";
  if (action.includes("remove") || action.includes("failed")) return "bg-red-900/50 text-red-400";
  return "bg-gray-800 text-gray-400";
}

function targetTypeLabel(type: string): string {
  const map: Record<string, string> = {
    skill: "技能",
    prompt: "提示词",
    tool_desc: "工具描述",
  };
  return map[type] || type;
}

function skillCategoryLabel(category: string): string {
  const map: Record<string, string> = {
    coding: "编程",
    frontend: "前端",
    backend: "后端",
    security: "安全",
    devops: "运维",
    data: "数据",
    research: "研究",
    product: "产品",
    ai: "AI",
    memory: "记忆",
    writing: "写作",
    marketing: "营销",
    support: "支持",
    education: "教育",
    browser: "浏览器",
    local: "本地",
    testing: "测试",
    quality: "质量",
  };
  return map[category] || category;
}
