"use client";

import { useState, useEffect, useCallback } from "react";
import {
  fetchMemory, addMemory, deleteMemory, searchMemory,
  fetchMemoryStats, fetchLayeredMemory, fetchProjectMemory, updateProjectMemory,
} from "@/lib/api";
import { Brain, Search, Plus, Trash2, ArrowLeft, FileText, Layers, Save } from "lucide-react";

interface MemoryEntry {
  id: string;
  key: string;
  value: string;
  category: string;
  access_count: number;
  created_at: string;
  updated_at: string;
}

interface LayeredMemory {
  project?: string;
  user_profile?: string;
  user?: Record<string, unknown>;
  agent?: Record<string, Array<Record<string, unknown>>>;
  session?: Record<string, Array<Record<string, unknown>>>;
  auto?: Array<{ topic: string; bytes: number }>;
  stats?: Record<string, unknown>;
}

export default function MemoryPage() {
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<"entries" | "layered" | "project" | "stats">("entries");

  // Add form
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [newCategory, setNewCategory] = useState("knowledge");
  const [showAdd, setShowAdd] = useState(false);

  // Project memory
  const [projectMem, setProjectMem] = useState("");
  const [projectMemEdit, setProjectMemEdit] = useState("");
  const [projectEditing, setProjectEditing] = useState(false);

  // Stats
  const [stats, setStats] = useState<Record<string, unknown> | null>(null);
  const [layered, setLayered] = useState<LayeredMemory | null>(null);

  const loadEntries = useCallback(async () => {
    setLoading(true);
    try {
      const data = query.trim()
        ? await searchMemory(query)
        : await fetchMemory();
      setEntries(Array.isArray(data) ? data : []);
    } catch { setEntries([]); }
    setLoading(false);
  }, [query]);

  useEffect(() => { loadEntries(); }, [loadEntries]);

  const loadProjectMemory = useCallback(async () => {
    try {
      const data = await fetchProjectMemory();
      setProjectMem(data.content || "");
      setProjectMemEdit(data.content || "");
    } catch { /* */ }
  }, []);

  const loadStats = useCallback(async () => {
    try {
      const data = await fetchMemoryStats();
      setStats(data);
    } catch { /* */ }
  }, []);

  const loadLayeredMemory = useCallback(async () => {
    try {
      const data = await fetchLayeredMemory();
      setLayered(data || null);
    } catch { setLayered(null); }
  }, []);

  useEffect(() => {
    if (tab === "layered") loadLayeredMemory();
    if (tab === "project") loadProjectMemory();
    if (tab === "stats") loadStats();
  }, [tab, loadLayeredMemory, loadProjectMemory, loadStats]);

  const handleAdd = async () => {
    if (!newKey.trim()) return;
    await addMemory(newKey, newValue, newCategory);
    setNewKey(""); setNewValue(""); setShowAdd(false);
    loadEntries();
  };

  const handleDelete = async (id: string) => {
    await deleteMemory(id);
    loadEntries();
  };

  const handleSaveProject = async () => {
    await updateProjectMemory(projectMemEdit);
    setProjectEditing(false);
    loadProjectMemory();
  };

  const categoryColors: Record<string, string> = {
    knowledge: "bg-blue-500/20 text-blue-300",
    preference: "bg-purple-500/20 text-purple-300",
    tech: "bg-green-500/20 text-green-300",
    personal: "bg-yellow-500/20 text-yellow-300",
    context: "bg-cyan-500/20 text-cyan-300",
    learned: "bg-orange-500/20 text-orange-300",
  };

  const categoryLabels: Record<string, string> = {
    knowledge: "知识",
    preference: "偏好",
    tech: "技术",
    personal: "个人",
    context: "上下文",
    learned: "经验",
    user_profile: "用户画像",
  };

  return (
    <div className="h-screen flex flex-col" style={{ background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-4 border-b" style={{ borderColor: "var(--border-color)" }}>
        <button onClick={() => window.location.href = "/"} className="p-2 rounded-lg hover:bg-white/5">
          <ArrowLeft size={18} />
        </button>
        <Brain size={22} style={{ color: "var(--accent)" }} />
        <h1 className="text-lg font-semibold">记忆管理</h1>
        <div className="flex-1" />
        <div className="flex gap-1 rounded-lg p-1" style={{ background: "var(--bg-secondary)" }}>
          {[
            { key: "entries" as const, icon: Brain, label: "记忆条目" },
            { key: "layered" as const, icon: Layers, label: "分层视图" },
            { key: "project" as const, icon: FileText, label: "项目记忆" },
            { key: "stats" as const, icon: Layers, label: "统计" },
          ].map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-colors ${
                tab === t.key ? "text-white" : "text-gray-400 hover:text-gray-200"
              }`}
              style={tab === t.key ? { background: "var(--accent)" } : {}}
            >
              <t.icon size={14} />
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-6">
        {tab === "entries" && (
          <div className="max-w-4xl mx-auto space-y-4">
            {/* Search + Add */}
            <div className="flex gap-3">
              <div className="flex-1 relative">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder="搜索记忆..."
                  className="w-full pl-10 pr-4 py-2.5 rounded-lg border text-sm"
                  style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                />
              </div>
              <button
                onClick={() => setShowAdd(!showAdd)}
                className="flex items-center gap-1.5 px-4 py-2.5 rounded-lg text-sm font-medium text-white"
                style={{ background: "var(--accent)" }}
              >
                <Plus size={16} /> 添加
              </button>
            </div>

            {/* Add Form */}
            {showAdd && (
              <div className="rounded-lg border p-4 space-y-3" style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}>
                <div className="grid grid-cols-2 gap-3">
                  <input value={newKey} onChange={e => setNewKey(e.target.value)} placeholder="键名（如 python_version）"
                    className="px-3 py-2 rounded-lg border text-sm" style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }} />
                  <select value={newCategory} onChange={e => setNewCategory(e.target.value)}
                    className="px-3 py-2 rounded-lg border text-sm" style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}>
                    <option value="knowledge">知识</option>
                    <option value="preference">偏好</option>
                    <option value="tech">技术</option>
                    <option value="personal">个人</option>
                    <option value="context">上下文</option>
                  </select>
                </div>
                <textarea value={newValue} onChange={e => setNewValue(e.target.value)} placeholder="值"
                  rows={3} className="w-full px-3 py-2 rounded-lg border text-sm resize-none"
                  style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }} />
                <div className="flex justify-end gap-2">
                  <button onClick={() => setShowAdd(false)} className="px-4 py-2 rounded-lg text-sm" style={{ color: "var(--text-secondary)" }}>取消</button>
                  <button onClick={handleAdd} className="px-4 py-2 rounded-lg text-sm font-medium text-white" style={{ background: "var(--accent)" }}>保存</button>
                </div>
              </div>
            )}

            {/* Entries */}
            {loading ? (
              <div className="text-center py-12" style={{ color: "var(--text-secondary)" }}>加载中...</div>
            ) : entries.length === 0 ? (
              <div className="text-center py-12" style={{ color: "var(--text-secondary)" }}>
                {query ? `未找到匹配"${query}"的记忆` : "暂无记忆条目"}
              </div>
            ) : (
              <div className="space-y-2">
                <div className="text-sm mb-2" style={{ color: "var(--text-secondary)" }}>
                  共 {entries.length} 条{query ? ` · 搜索: "${query}"` : ""}
                </div>
                {entries.map(e => (
                  <div key={e.id} className="flex items-start gap-3 rounded-lg border p-4 hover:border-[var(--accent)] transition-colors"
                    style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className={`text-xs px-2 py-0.5 rounded-full ${categoryColors[e.category] || "bg-gray-500/20 text-gray-300"}`}>
                          {categoryLabels[e.category] || e.category}
                        </span>
                        <span className="font-medium text-sm truncate">{e.key}</span>
                        <span className="text-xs ml-auto" style={{ color: "var(--text-secondary)" }}>
                          访问 {e.access_count} 次
                        </span>
                      </div>
                      <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>{e.value}</p>
                    </div>
                    <button onClick={() => handleDelete(e.id)} className="p-1.5 rounded-lg hover:bg-red-500/20 text-red-400">
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {tab === "layered" && (
          <div className="max-w-5xl mx-auto space-y-4">
            <div>
              <h2 className="text-base font-medium">分层记忆总览</h2>
              <p className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
                AI 实际会合并项目、用户、Agent、会话和自动记忆。这里按来源拆开显示，方便判断“有没有记住”。
              </p>
            </div>
            {!layered ? (
              <div style={{ color: "var(--text-secondary)" }}>加载中...</div>
            ) : (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <MemoryLayerCard title="项目记忆" subtitle="所有 Agent 共享" empty="项目记忆为空">
                  {layered.project ? <pre className="text-xs whitespace-pre-wrap">{layered.project}</pre> : null}
                </MemoryLayerCard>
                <MemoryLayerCard title="用户画像" subtitle="长期用户偏好/个人资料" empty="用户画像为空">
                  {layered.user_profile ? <pre className="text-xs whitespace-pre-wrap">{layered.user_profile}</pre> : null}
                </MemoryLayerCard>
                <MemoryLayerCard title="用户记忆" subtitle="偏好和上下文" empty="用户记忆为空">
                  <MemoryObject value={layered.user} />
                </MemoryLayerCard>
                <MemoryLayerCard title="Agent 记忆" subtitle="不同 Agent 类型自己的经验" empty="Agent 记忆为空">
                  <MemoryObject value={layered.agent} />
                </MemoryLayerCard>
                <MemoryLayerCard title="会话记忆" subtitle="当前运行期内的短期上下文" empty="会话记忆为空">
                  <MemoryObject value={layered.session} />
                </MemoryLayerCard>
                <MemoryLayerCard title="自动记忆文件" subtitle="按主题累积的自动记忆" empty="自动记忆为空">
                  {(layered.auto || []).length > 0 ? <div className="space-y-2">
                    {(layered.auto || []).map(item => (
                      <div key={item.topic} className="flex items-center justify-between text-sm">
                        <span>{item.topic}</span>
                        <span style={{ color: "var(--text-secondary)" }}>{item.bytes} 字节</span>
                      </div>
                    ))}
                  </div> : null}
                </MemoryLayerCard>
              </div>
            )}
          </div>
        )}

        {tab === "project" && (
          <div className="max-w-4xl mx-auto space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-medium">MEMORY.md — 项目记忆</h2>
                <p className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>所有 Agent 共享的项目知识</p>
              </div>
              {projectEditing ? (
                <div className="flex gap-2">
                  <button onClick={() => { setProjectEditing(false); setProjectMemEdit(projectMem); }}
                    className="px-3 py-1.5 rounded-lg text-sm" style={{ color: "var(--text-secondary)" }}>取消</button>
                  <button onClick={handleSaveProject}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium text-white" style={{ background: "var(--accent)" }}>
                    <Save size={14} /> 保存
                  </button>
                </div>
              ) : (
                <button onClick={() => setProjectEditing(true)}
                  className="px-3 py-1.5 rounded-lg text-sm" style={{ background: "var(--accent)", color: "white" }}>编辑</button>
              )}
            </div>
            {projectEditing ? (
              <textarea
                value={projectMemEdit}
                onChange={e => setProjectMemEdit(e.target.value)}
                rows={20}
                className="w-full px-4 py-3 rounded-lg border text-sm font-mono resize-none"
                style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
              />
            ) : (
              <pre className="rounded-lg border p-4 text-sm font-mono whitespace-pre-wrap overflow-auto max-h-[calc(100vh-200px)]"
                style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}>
                {projectMem || "(空)"}
              </pre>
            )}
          </div>
        )}

        {tab === "stats" && (
          <div className="max-w-4xl mx-auto space-y-4">
            <h2 className="text-base font-medium">记忆系统统计</h2>
            {stats ? (
              <pre className="rounded-lg border p-4 text-sm font-mono whitespace-pre-wrap overflow-auto"
                style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}>
                {JSON.stringify(stats, null, 2)}
              </pre>
            ) : (
              <div style={{ color: "var(--text-secondary)" }}>加载中...</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function MemoryLayerCard({ title, subtitle, empty, children }: { title: string; subtitle: string; empty: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border p-4 min-h-40" style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}>
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <div className="font-medium text-sm">{title}</div>
          <div className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>{subtitle}</div>
        </div>
      </div>
      <div className="max-h-64 overflow-auto">{children || <div className="text-sm py-6 text-center" style={{ color: "var(--text-secondary)" }}>{empty}</div>}</div>
    </div>
  );
}

function MemoryObject({ value }: { value: unknown }) {
  if (!value || (typeof value === "object" && Object.keys(value as Record<string, unknown>).length === 0)) {
    return <div className="text-sm py-6 text-center" style={{ color: "var(--text-secondary)" }}>暂无数据</div>;
  }
  return (
    <pre className="text-xs whitespace-pre-wrap">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
