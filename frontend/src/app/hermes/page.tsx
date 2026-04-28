"use client";

import { useState, useEffect, useCallback } from "react";
import {
  fetchSubagents, createSubagent, deleteSubagent, spawnSubagent,
  fetchSubagentInstances, fetchSubagentInstance, cleanupSubagentWorktree,
  fetchHooks, registerHook, deleteHook, enableHook, disableHook, fetchHookHistory,
  fetchCronJobs, addCronJob, deleteCronJob, runCronJob, enableCronJob, disableCronJob,
  fetchPlugins, enablePlugin, disablePlugin, discoverPlugins,
  gepaEvolve,
  fetchMcpServers, addMcpServer, removeMcpServer, fetchMcpTools, discoverMcpTools,
  fetchMemoryProviders, activateMemoryProvider, deactivateMemoryProvider,
  fetchContextEngines, activateContextEngine, deactivateContextEngine,
  fetchPermissionScopes, reloadPermissions,
} from "@/lib/api";
import {
  ArrowLeft, Users, Zap, Clock, Package, Dna, Play, Trash2, Plus, RefreshCw,
  CheckCircle2, XCircle, Pause, GitBranch, Terminal, AlertCircle,
  Server, Brain, Shield,
  type LucideIcon,
} from "lucide-react";

type Tab = "subagents" | "hooks" | "cron" | "plugins" | "gepa" | "mcp" | "providers" | "scopes";

export default function HermesPage() {
  const [tab, setTab] = useState<Tab>("subagents");

  return (
    <div className="h-screen flex flex-col" style={{ background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      <Header tab={tab} setTab={setTab} />
      <div className="flex-1 overflow-auto">
        {tab === "subagents" && <SubagentsTab />}
        {tab === "hooks" && <HooksTab />}
        {tab === "mcp" && <MCPTab />}
        {tab === "providers" && <ProvidersTab />}
        {tab === "scopes" && <ScopesTab />}
        {tab === "cron" && <CronTab />}
        {tab === "plugins" && <PluginsTab />}
        {tab === "gepa" && <GepaTab />}
      </div>
    </div>
  );
}

// ============================================================================
// Header
// ============================================================================
function Header({ tab, setTab }: { tab: Tab; setTab: (t: Tab) => void }) {
  const tabs: { key: Tab; icon: LucideIcon; label: string }[] = [
    { key: "subagents", icon: Users, label: "子 Agent" },
    { key: "hooks", icon: Zap, label: "钩子" },
    { key: "mcp", icon: Server, label: "MCP" },
    { key: "providers", icon: Brain, label: "记忆/上下文" },
    { key: "scopes", icon: Shield, label: "权限作用域" },
    { key: "cron", icon: Clock, label: "定时" },
    { key: "plugins", icon: Package, label: "插件" },
    { key: "gepa", icon: Dna, label: "GEPA" },
  ];
  return (
    <div className="flex items-center gap-3 px-6 py-4 border-b" style={{ borderColor: "var(--border-color)" }}>
      <button onClick={() => window.location.href = "/"} className="p-2 rounded-lg hover:bg-white/5">
        <ArrowLeft size={18} />
      </button>
      <h1 className="text-lg font-semibold">Hermes 控制台</h1>
      <div className="flex-1" />
      <div className="flex gap-1 rounded-lg p-1" style={{ background: "var(--bg-secondary)" }}>
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-colors ${tab === t.key ? "text-white" : "text-gray-400 hover:text-gray-200"}`}
            style={tab === t.key ? { background: "var(--accent)" } : {}}>
            <t.icon size={14} />{t.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// ============================================================================
// Subagents Tab
// ============================================================================
interface SubagentConfig {
  name: string;
  description: string;
  prompt?: string;
  source?: string;
  model?: string;
  max_turns?: number;
  isolation?: string;
  tools?: string[];
  disallowed_tools?: string[];
  permission_mode?: string;
  background?: boolean;
  skills?: string[];
  memory?: string | null;
  mcp_servers?: Record<string, unknown>[];
}

interface SubagentInstance {
  agent_id: string;
  config: SubagentConfig;
  status: string;
  started_at: string;
  finished_at: string;
  turn_count: number;
  result_summary: string;
  workdir?: string | null;
  worktree_branch?: string | null;
}

function SubagentsTab() {
  const [agents, setAgents] = useState<SubagentConfig[]>([]);
  const [instances, setInstances] = useState<SubagentInstance[]>([]);
  const [showNew, setShowNew] = useState(false);
  const [showSpawn, setShowSpawn] = useState<SubagentConfig | null>(null);
  const [inspect, setInspect] = useState<SubagentInstance | null>(null);

  const load = useCallback(async () => {
    const [a, i] = await Promise.all([
      fetchSubagents().catch(() => ({ agents: [] })),
      fetchSubagentInstances().catch(() => ({ instances: [] })),
    ]);
    setAgents(a.agents || []);
    setInstances(i.instances || []);
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, [load]);

  return (
    <div className="p-6 space-y-6">
      {/* Agent Definitions */}
      <section>
        <SectionHeader title="子 Agent 定义" icon={Users} action={
          <button onClick={() => setShowNew(true)} className="btn-primary">
            <Plus size={14} /> 新建
          </button>
        } />
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mt-3">
          {agents.map(a => (
            <div key={a.name} className="card">
              <div className="flex items-start justify-between">
                <div>
                  <div className="font-semibold">{a.name}</div>
                  <div className="text-xs text-gray-400 mt-1">{a.description}</div>
                </div>
                <div className="flex gap-1">
                  <button onClick={() => setShowSpawn(a)} className="icon-btn" title="启动">
                    <Play size={14} />
                  </button>
                  {a.source !== "builtin" && (
                    <button onClick={async () => {
                      if (!confirm(`删除 ${a.name}?`)) return;
                      await deleteSubagent(a.name); load();
                    }} className="icon-btn text-red-400" title="删除">
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-1 text-xs">
                {a.source && <span className="tag">{sourceText(a.source)}</span>}
                {a.model && <span className="tag">模型：{a.model}</span>}
                {a.max_turns && <span className="tag">轮数：{a.max_turns}</span>}
                {a.permission_mode && <span className="tag">权限：{a.permission_mode}</span>}
                {a.isolation && <span className="tag text-amber-300">{a.isolation}</span>}
                {a.tools?.length ? <span className="tag">{a.tools.length} 个工具</span> : null}
                {a.skills?.length ? <span className="tag">{a.skills.length} 个技能</span> : null}
                {a.memory ? <span className="tag">记忆：{a.memory}</span> : null}
                {a.mcp_servers?.length ? <span className="tag">{a.mcp_servers.length} MCP</span> : null}
              </div>
            </div>
          ))}
          {agents.length === 0 && <EmptyState text="尚无子 Agent" />}
        </div>
      </section>

      {/* Running Instances */}
      <section>
        <SectionHeader title={`运行实例 (${instances.length})`} icon={Terminal} />
        <div className="mt-3 space-y-2">
          {instances.slice().reverse().map(i => (
            <div key={i.agent_id} className="card flex items-center gap-4">
              <StatusBadge status={i.status} />
              <div className="flex-1 min-w-0">
                <div className="font-mono text-sm truncate">{i.agent_id}</div>
                <div className="text-xs text-gray-400 truncate">
                  {i.config.name} · {i.turn_count} 轮 · {fmtTime(i.started_at)}
                  {i.workdir && <span className="ml-2 text-amber-300"><GitBranch size={10} className="inline" /> {i.worktree_branch}</span>}
                </div>
              </div>
              <div className="flex gap-1">
                <button onClick={() => setInspect(i)} className="icon-btn" title="详情">查看</button>
                {i.workdir && (
                  <button onClick={async () => {
                    if (!confirm("清理 worktree?")) return;
                    await cleanupSubagentWorktree(i.agent_id, false); load();
                  }} className="icon-btn text-amber-400" title="清理 worktree">
                    <GitBranch size={14} />
                  </button>
                )}
              </div>
            </div>
          ))}
          {instances.length === 0 && <EmptyState text="尚无运行实例" />}
        </div>
      </section>

      {showNew && <NewSubagentModal onClose={() => setShowNew(false)} onCreated={load} />}
      {showSpawn && <SpawnSubagentModal agent={showSpawn} onClose={() => setShowSpawn(null)} onSpawned={load} />}
      {inspect && <SubagentInspectModal initial={inspect} onClose={() => setInspect(null)} />}
    </div>
  );
}

function NewSubagentModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [maxTurns, setMaxTurns] = useState(5);
  const [isolation, setIsolation] = useState("");
  const [tools, setTools] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    if (!name.trim()) { alert("请填写名称"); return; }
    setSubmitting(true);
    try {
      const payload: Parameters<typeof createSubagent>[0] = {
        name: name.trim(), description: description.trim(), prompt: prompt.trim(),
        max_turns: maxTurns,
      };
      if (model.trim()) payload.model = model.trim();
      if (isolation) payload.isolation = isolation;
      if (tools.trim()) payload.tools = tools.split(",").map(t => t.trim()).filter(Boolean);
      const result = await createSubagent(payload);
      if (result.ok === false) { alert(result.message || "创建失败"); return; }
      onCreated();
      onClose();
    } catch (e) {
      alert(String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title="新建子 Agent" onClose={onClose}>
      <Field label="名称"><input value={name} onChange={e => setName(e.target.value)} className="input" placeholder="e.g. my_research_agent" /></Field>
      <Field label="描述"><input value={description} onChange={e => setDescription(e.target.value)} className="input" /></Field>
      <Field label="系统提示词"><textarea value={prompt} onChange={e => setPrompt(e.target.value)} className="input min-h-[120px]" /></Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="模型 (可选)"><input value={model} onChange={e => setModel(e.target.value)} className="input" placeholder="继承默认" /></Field>
        <Field label="最大轮数"><input type="number" value={maxTurns} onChange={e => setMaxTurns(parseInt(e.target.value) || 5)} className="input" /></Field>
      </div>
      <Field label="隔离模式">
        <select value={isolation} onChange={e => setIsolation(e.target.value)} className="input">
          <option value="">无 (共享 cwd)</option>
          <option value="worktree">Git worktree (隔离分支)</option>
        </select>
      </Field>
      <Field label="工具 (逗号分隔)"><input value={tools} onChange={e => setTools(e.target.value)} className="input" placeholder="read_file,write_file" /></Field>
      <div className="flex gap-2 justify-end mt-4">
        <button onClick={onClose} className="btn-secondary">取消</button>
        <button onClick={submit} disabled={submitting} className="btn-primary">创建</button>
      </div>
    </Modal>
  );
}

function SpawnSubagentModal({ agent, onClose, onSpawned }: { agent: SubagentConfig; onClose: () => void; onSpawned: () => void }) {
  const [task, setTask] = useState("");
  const [background, setBackground] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    if (!task.trim()) { alert("请填写任务"); return; }
    setSubmitting(true);
    try {
      await spawnSubagent({ agent_name: agent.name, task_prompt: task.trim(), background });
      onSpawned();
      onClose();
    } catch (e) {
      alert(String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`启动: ${agent.name}`} onClose={onClose}>
      <Field label="任务"><textarea value={task} onChange={e => setTask(e.target.value)} className="input min-h-[120px]" placeholder="该 agent 要完成的任务..." /></Field>
      <label className="flex items-center gap-2 mt-2">
        <input type="checkbox" checked={background} onChange={e => setBackground(e.target.checked)} />
        <span className="text-sm">后台运行 (不阻塞)</span>
      </label>
      <div className="flex gap-2 justify-end mt-4">
        <button onClick={onClose} className="btn-secondary">取消</button>
        <button onClick={submit} disabled={submitting} className="btn-primary">启动</button>
      </div>
    </Modal>
  );
}

function SubagentInspectModal({ initial, onClose }: { initial: SubagentInstance; onClose: () => void }) {
  const [instance, setInstance] = useState<SubagentInstance>(initial);

  useEffect(() => {
    const t = setInterval(async () => {
      try {
        const data = await fetchSubagentInstance(initial.agent_id);
        if (data?.agent_id) setInstance(data);
      } catch { /* */ }
    }, 2000);
    return () => clearInterval(t);
  }, [initial.agent_id]);

  return (
    <Modal title={`实例: ${instance.agent_id}`} onClose={onClose} wide>
      <div className="space-y-3 text-sm">
        <div className="flex gap-4 flex-wrap">
          <StatusBadge status={instance.status} />
          <div className="text-gray-400">Agent: <span className="text-white">{instance.config.name}</span></div>
          <div className="text-gray-400">轮数: <span className="text-white">{instance.turn_count}</span></div>
          {instance.workdir && <div className="text-amber-300"><GitBranch size={12} className="inline" /> {instance.worktree_branch}</div>}
        </div>
        {instance.result_summary && (
          <div>
            <div className="text-gray-400 text-xs mb-1">结果摘要</div>
            <pre className="p-3 rounded text-xs whitespace-pre-wrap break-words" style={{ background: "var(--bg-secondary)" }}>{instance.result_summary}</pre>
          </div>
        )}
      </div>
    </Modal>
  );
}

// ============================================================================
// Hooks Tab
// ============================================================================
interface Hook {
  name: string;
  event: string;
  description?: string;
  enabled: boolean;
  handlers: Array<{ handler_type: string; command?: string; prompt?: string; agent?: string }>;
}

const HOOK_EVENTS = [
  "SessionStart", "SessionEnd", "UserPromptSubmit",
  "PreToolUse", "PostToolUse", "Stop",
  "SubagentStart", "SubagentStop",
  "PreCompact", "PostCompact", "Notification",
  "FileEdit", "FileCreate", "FileDelete",
  "Error", "RateLimitHit",
];

function HooksTab() {
  const [hooks, setHooks] = useState<Hook[]>([]);
  const [history, setHistory] = useState<Array<{ event: string; timestamp: string; context?: unknown; decision?: string }>>([]);
  const [showNew, setShowNew] = useState(false);

  const load = useCallback(async () => {
    const [h, hi] = await Promise.all([
      fetchHooks().catch(() => ({ hooks: [] })),
      fetchHookHistory(30).catch(() => ({ history: [] })),
    ]);
    setHooks(h.hooks || []);
    setHistory(hi.history || []);
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, [load]);

  return (
    <div className="p-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div className="lg:col-span-2 space-y-3">
        <SectionHeader title={`已注册钩子 (${hooks.length})`} icon={Zap} action={
          <button onClick={() => setShowNew(true)} className="btn-primary"><Plus size={14} /> 注册钩子</button>
        } />
        {hooks.map(h => (
          <div key={h.name} className="card">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-semibold truncate">{h.name}</span>
                  <span className="tag text-blue-300">{eventText(h.event)}</span>
                  {!h.enabled && <span className="tag text-gray-500">已禁用</span>}
                </div>
                {h.description && <div className="text-xs text-gray-400 mt-1">{h.description}</div>}
                <div className="text-xs text-gray-500 mt-2">
                  {h.handlers.map((hd, i) => (
                    <span key={i} className="mr-2">
                      {handlerTypeText(hd.handler_type)}：<code className="text-gray-300">{hd.command || hd.prompt || hd.agent || ""}</code>
                    </span>
                  ))}
                </div>
              </div>
              <div className="flex gap-1">
                {h.enabled ? (
                  <button onClick={async () => { await disableHook(h.name); load(); }} className="icon-btn" title="禁用">
                    <Pause size={14} />
                  </button>
                ) : (
                  <button onClick={async () => { await enableHook(h.name); load(); }} className="icon-btn" title="启用">
                    <CheckCircle2 size={14} />
                  </button>
                )}
                <button onClick={async () => {
                  if (!confirm(`删除 ${h.name}?`)) return;
                  await deleteHook(h.name); load();
                }} className="icon-btn text-red-400"><Trash2 size={14} /></button>
              </div>
            </div>
          </div>
        ))}
        {hooks.length === 0 && <EmptyState text="尚无钩子" />}
      </div>

      <div className="space-y-3">
        <SectionHeader title="触发历史" icon={Clock} />
        <div className="space-y-2 max-h-[70vh] overflow-auto">
          {history.slice().reverse().map((h, i) => (
            <div key={i} className="card py-2">
              <div className="flex items-center gap-2 text-xs">
                <span className="tag text-blue-300">{eventText(h.event)}</span>
                <span className="text-gray-400">{fmtTime(h.timestamp)}</span>
                {h.decision && <span className={h.decision === "deny" ? "text-red-400" : "text-green-400"}>{decisionText(h.decision)}</span>}
              </div>
            </div>
          ))}
          {history.length === 0 && <EmptyState text="暂无触发" />}
        </div>
      </div>

      {showNew && <NewHookModal onClose={() => setShowNew(false)} onCreated={load} />}
    </div>
  );
}

function NewHookModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [event, setEvent] = useState("UserPromptSubmit");
  const [description, setDescription] = useState("");
  const [handlerType, setHandlerType] = useState<"command" | "prompt" | "agent">("command");
  const [command, setCommand] = useState("");
  const [matcher, setMatcher] = useState("");

  async function submit() {
    if (!name.trim() || !command.trim()) { alert("请填名称和处理器"); return; }
    const handler: { handler_type: string; command?: string; prompt?: string; agent?: string } = { handler_type: handlerType };
    if (handlerType === "command") handler.command = command.trim();
    else if (handlerType === "prompt") handler.prompt = command.trim();
    else handler.agent = command.trim();
    const result = await registerHook({
      name: name.trim(), event, description: description.trim(),
      handlers: [handler], matcher: matcher.trim() || undefined,
    });
    if (result.ok === false) { alert(result.message || "注册失败"); return; }
    onCreated();
    onClose();
  }

  return (
    <Modal title="注册钩子" onClose={onClose}>
      <Field label="名称"><input value={name} onChange={e => setName(e.target.value)} className="input" /></Field>
      <Field label="事件">
        <select value={event} onChange={e => setEvent(e.target.value)} className="input">
          {HOOK_EVENTS.map(ev => <option key={ev} value={ev}>{eventText(ev)}</option>)}
        </select>
      </Field>
      <Field label="描述 (可选)"><input value={description} onChange={e => setDescription(e.target.value)} className="input" /></Field>
      <Field label="匹配正则（可选）"><input value={matcher} onChange={e => setMatcher(e.target.value)} className="input" placeholder='例如 ".*sensitive.*"' /></Field>
      <Field label="处理器类型">
        <select value={handlerType} onChange={e => setHandlerType(e.target.value as "command" | "prompt" | "agent")} className="input">
          <option value="command">Shell 命令</option>
          <option value="prompt">提示词（LLM 决策）</option>
          <option value="agent">子 Agent 调用</option>
        </select>
      </Field>
      <Field label={handlerType === "command" ? "命令" : handlerType === "prompt" ? "提示词" : "Agent 名"}>
        <textarea value={command} onChange={e => setCommand(e.target.value)} className="input min-h-[80px]" />
      </Field>
      <div className="flex gap-2 justify-end mt-4">
        <button onClick={onClose} className="btn-secondary">取消</button>
        <button onClick={submit} className="btn-primary">注册</button>
      </div>
    </Modal>
  );
}

// ============================================================================
// Cron Tab
// ============================================================================
interface CronJob {
  name: string;
  schedule: string;
  action: string;
  action_type: string;
  enabled: boolean;
  last_run?: string;
  next_run?: string;
  run_count?: number;
}

function CronTab() {
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [showNew, setShowNew] = useState(false);

  const load = useCallback(async () => {
    const j = await fetchCronJobs().catch(() => ({ jobs: [] }));
    setJobs(j.jobs || []);
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);

  return (
    <div className="p-6">
      <SectionHeader title={`定时任务 (${jobs.length})`} icon={Clock} action={
        <button onClick={() => setShowNew(true)} className="btn-primary"><Plus size={14} /> 新建</button>
      } />
      <div className="space-y-2 mt-3">
        {jobs.map(j => (
          <div key={j.name} className="card">
            <div className="flex items-center gap-4 flex-wrap">
              <div className="flex items-center gap-2">
                {j.enabled ? <CheckCircle2 size={14} className="text-green-400" /> : <Pause size={14} className="text-gray-500" />}
                <span className="font-semibold">{j.name}</span>
              </div>
              <code className="text-xs px-2 py-1 rounded" style={{ background: "var(--bg-secondary)" }}>{j.schedule}</code>
              <span className="tag">{actionTypeText(j.action_type)}</span>
              <div className="text-xs text-gray-400 flex-1 truncate">{j.action}</div>
              <div className="flex gap-1">
                <button onClick={async () => {
                  const r = await runCronJob(j.name);
                  alert(r.output || r.error || "已执行");
                }} className="icon-btn text-blue-400" title="立即执行"><Play size={14} /></button>
                {j.enabled ? (
                  <button onClick={async () => { await disableCronJob(j.name); load(); }} className="icon-btn" title="禁用"><Pause size={14} /></button>
                ) : (
                  <button onClick={async () => { await enableCronJob(j.name); load(); }} className="icon-btn" title="启用"><CheckCircle2 size={14} /></button>
                )}
                <button onClick={async () => {
                  if (!confirm(`删除 ${j.name}?`)) return;
                  await deleteCronJob(j.name); load();
                }} className="icon-btn text-red-400"><Trash2 size={14} /></button>
              </div>
            </div>
            {(j.last_run || j.run_count) && (
              <div className="text-xs text-gray-500 mt-2">
                {j.last_run && <span>上次: {fmtTime(j.last_run)} · </span>}
                {j.run_count != null && <span>累计: {j.run_count} 次</span>}
              </div>
            )}
          </div>
        ))}
        {jobs.length === 0 && <EmptyState text="尚无定时任务" />}
      </div>
      {showNew && <NewCronModal onClose={() => setShowNew(false)} onCreated={load} />}
    </div>
  );
}

function NewCronModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [schedule, setSchedule] = useState("0 9 * * *");
  const [actionType, setActionType] = useState<"command" | "prompt" | "skill">("command");
  const [action, setAction] = useState("");

  async function submit() {
    if (!name.trim() || !action.trim()) { alert("请填名称和动作"); return; }
    const result = await addCronJob({
      name: name.trim(), schedule: schedule.trim(), action: action.trim(),
      action_type: actionType, enabled: true,
    });
    if (result.ok === false) { alert(result.message || "创建失败"); return; }
    onCreated();
    onClose();
  }

  return (
    <Modal title="新建定时任务" onClose={onClose}>
      <Field label="名称"><input value={name} onChange={e => setName(e.target.value)} className="input" /></Field>
      <Field label="Cron 表达式">
        <input value={schedule} onChange={e => setSchedule(e.target.value)} className="input" placeholder="0 9 * * *" />
        <div className="text-xs text-gray-500 mt-1">格式：分 时 日 月 周，例如 `*/5 * * * *` 表示每 5 分钟。</div>
      </Field>
      <Field label="动作类型">
        <select value={actionType} onChange={e => setActionType(e.target.value as "command" | "prompt" | "skill")} className="input">
          <option value="command">Shell 命令</option>
          <option value="prompt">提示词（发送到聊天）</option>
          <option value="skill">技能执行</option>
        </select>
      </Field>
      <Field label={actionType === "command" ? "命令" : actionType === "prompt" ? "提示词" : "技能名"}>
        <textarea value={action} onChange={e => setAction(e.target.value)} className="input min-h-[80px]" />
      </Field>
      <div className="flex gap-2 justify-end mt-4">
        <button onClick={onClose} className="btn-secondary">取消</button>
        <button onClick={submit} className="btn-primary">创建</button>
      </div>
    </Modal>
  );
}

// ============================================================================
// Plugins Tab
// ============================================================================
interface Plugin {
  name: string;
  version?: string;
  description?: string;
  source?: string;
  enabled?: boolean;
  path?: string;
  tools?: string[];
  hooks?: unknown[];
}

function PluginsTab() {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    const r = await fetchPlugins().catch(() => ({ plugins: [] }));
    setPlugins(r.plugins || []);
  }, []);

  useEffect(() => { load(); }, [load]);

  async function rediscover() {
    setLoading(true);
    try {
      await discoverPlugins();
      await load();
    } finally { setLoading(false); }
  }

  return (
    <div className="p-6">
      <SectionHeader title={`插件 (${plugins.length})`} icon={Package} action={
        <button onClick={rediscover} disabled={loading} className="btn-primary">
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> 重新发现
        </button>
      } />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mt-3">
        {plugins.map(p => (
          <div key={p.name} className="card">
            <div className="flex items-start justify-between">
              <div>
                <div className="font-semibold">{p.name}</div>
                <div className="text-xs text-gray-400 mt-1">v{p.version || "?"} · {p.source ? sourceText(p.source) : "未知来源"}</div>
                {p.description && <div className="text-xs text-gray-500 mt-2">{p.description}</div>}
              </div>
              {p.enabled ? (
                <button onClick={async () => { await disablePlugin(p.name); load(); }} className="icon-btn text-green-400" title="点击禁用"><CheckCircle2 size={14} /></button>
              ) : (
                <button onClick={async () => { await enablePlugin(p.name); load(); }} className="icon-btn text-gray-500" title="点击启用"><XCircle size={14} /></button>
              )}
            </div>
            <div className="mt-3 flex flex-wrap gap-1 text-xs">
              {p.tools?.length ? <span className="tag">{p.tools.length} 个工具</span> : null}
              {p.hooks?.length ? <span className="tag">{p.hooks.length} 个钩子</span> : null}
            </div>
            {p.path && <div className="text-xs text-gray-600 mt-2 truncate">{p.path}</div>}
          </div>
        ))}
        {plugins.length === 0 && <EmptyState text="未发现插件。放置到 ~/.hermes/plugins/ 或 .hermes/plugins/" />}
      </div>
    </div>
  );
}

// ============================================================================
// GEPA Tab
// ============================================================================
interface GepaResult {
  best_prompt?: string;
  best_score?: number;
  generations?: Array<{ generation: number; best_score: number; avg_score: number }>;
  [key: string]: unknown;
}

function GepaTab() {
  const [initialPrompt, setInitialPrompt] = useState("");
  const [numGenerations, setNumGenerations] = useState(5);
  const [populationSize, setPopulationSize] = useState(6);
  const [useLlm, setUseLlm] = useState(false);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<GepaResult | null>(null);
  const [error, setError] = useState("");

  async function runEvolve() {
    if (!initialPrompt.trim()) { alert("请填写初始提示词"); return; }
    setRunning(true);
    setError("");
    setResult(null);
    try {
      const r = await gepaEvolve({
        initial_prompt: initialPrompt, num_generations: numGenerations,
        population_size: populationSize, use_llm: useLlm,
      });
      setResult(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="p-6 grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div className="space-y-3">
        <SectionHeader title="GEPA 遗传进化" icon={Dna} />
        <Field label="初始提示词">
          <textarea value={initialPrompt} onChange={e => setInitialPrompt(e.target.value)} className="input min-h-[160px]" placeholder="例如：你是一个严谨的编程助手..." />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="进化代数"><input type="number" value={numGenerations} onChange={e => setNumGenerations(parseInt(e.target.value) || 5)} className="input" /></Field>
          <Field label="种群大小"><input type="number" value={populationSize} onChange={e => setPopulationSize(parseInt(e.target.value) || 6)} className="input" /></Field>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={useLlm} onChange={e => setUseLlm(e.target.checked)} />
          <span>使用 LLM 变异 (更慢更好)，关闭则用规则变异</span>
        </label>
        <button onClick={runEvolve} disabled={running} className="btn-primary w-full">
          {running ? <><RefreshCw size={14} className="animate-spin" /> 进化中...</> : <><Play size={14} /> 开始进化</>}
        </button>
        {error && (
          <div className="card flex items-start gap-2 text-red-400">
            <AlertCircle size={14} className="mt-0.5" /><span className="text-xs">{error}</span>
          </div>
        )}
      </div>
      <div className="space-y-3">
        <SectionHeader title="结果" icon={CheckCircle2} />
        {result ? (
          <>
            {typeof result.best_score === "number" && (
              <div className="card">
                <div className="text-xs text-gray-400">最优得分</div>
                <div className="text-2xl font-bold mt-1">{result.best_score.toFixed(2)}</div>
              </div>
            )}
            {result.best_prompt && (
              <div className="card">
                <div className="text-xs text-gray-400 mb-1">最优提示词</div>
                <pre className="p-3 rounded text-xs whitespace-pre-wrap break-words" style={{ background: "var(--bg-secondary)" }}>{result.best_prompt}</pre>
              </div>
            )}
            {Array.isArray(result.generations) && (
              <div className="card">
                <div className="text-xs text-gray-400 mb-2">代数趋势</div>
                <div className="space-y-1">
                  {result.generations.map(g => (
                    <div key={g.generation} className="flex items-center gap-2 text-xs">
                      <span className="w-8 text-gray-500">G{g.generation}</span>
                      <div className="flex-1 h-2 rounded overflow-hidden" style={{ background: "var(--bg-secondary)" }}>
                        <div className="h-full bg-blue-500" style={{ width: `${Math.max(5, g.best_score)}%` }} />
                      </div>
                      <span className="w-12 text-right">{g.best_score?.toFixed(1)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        ) : <EmptyState text="尚未运行" />}
      </div>
    </div>
  );
}

// ============================================================================
// MCP Tab
// ============================================================================
interface McpServer {
  name: string;
  transport?: string;
  url?: string;
  command?: string;
  args?: string[];
  enabled?: boolean;
  tools?: string[];
}

function MCPTab() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [showNew, setShowNew] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    const data = await fetchMcpServers().catch(() => []);
    setServers(Array.isArray(data) ? data : (data?.servers || []));
  }, []);

  useEffect(() => { load(); }, [load]);

  async function rediscover() {
    setLoading(true);
    try { await discoverMcpTools(); await load(); }
    finally { setLoading(false); }
  }

  return (
    <div className="p-6">
      <SectionHeader title={`MCP 服务 (${servers.length})`} icon={Server} action={
        <div className="flex gap-2">
          <button onClick={rediscover} disabled={loading} className="btn-secondary">
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> 重新发现
          </button>
          <button onClick={() => setShowNew(true)} className="btn-primary"><Plus size={14} /> 添加</button>
        </div>
      } />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
        {servers.map(s => (
          <div key={s.name} className="card">
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-semibold">{s.name}</span>
                  <span className="tag">{s.transport || "http"}</span>
                  {s.enabled ? <CheckCircle2 size={14} className="text-green-400" /> : <XCircle size={14} className="text-gray-500" />}
                </div>
                <div className="text-xs text-gray-400 mt-1 font-mono break-all">
                  {s.transport === "stdio"
                    ? `${s.command || "?"} ${(s.args || []).join(" ")}`
                    : s.url || "—"}
                </div>
              </div>
              <button onClick={async () => {
                if (!confirm(`删除 ${s.name}?`)) return;
                await removeMcpServer(s.name); load();
              }} className="icon-btn text-red-400"><Trash2 size={14} /></button>
            </div>
            {s.tools?.length ? (
              <div className="mt-3 flex flex-wrap gap-1 text-xs">
                {s.tools.map(t => <span key={t} className="tag">{t}</span>)}
              </div>
            ) : null}
          </div>
        ))}
        {servers.length === 0 && <EmptyState text="尚未配置 MCP 服务。添加 stdio (如 npx mcp-server-filesystem) 或 HTTP 端点。" />}
      </div>
      {showNew && <NewMcpModal onClose={() => setShowNew(false)} onCreated={load} />}
    </div>
  );
}

function NewMcpModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [transport, setTransport] = useState<"http" | "stdio">("stdio");
  const [url, setUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [command, setCommand] = useState("");
  const [argsStr, setArgsStr] = useState("");
  const [envStr, setEnvStr] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    if (!name.trim()) { alert("请填写名称"); return; }
    setSubmitting(true);
    try {
      const payload: Parameters<typeof addMcpServer>[0] = {
        name: name.trim(), transport, enabled: true,
      };
      if (transport === "http") {
        if (!url.trim()) { alert("HTTP 需要 URL"); return; }
        payload.url = url.trim();
        if (apiKey.trim()) payload.api_key = apiKey.trim();
      } else {
        if (!command.trim()) { alert("stdio 需要命令"); return; }
        payload.command = command.trim();
        if (argsStr.trim()) payload.args = argsStr.trim().split(/\s+/);
        if (envStr.trim()) {
          const env: Record<string, string> = {};
          for (const line of envStr.split("\n")) {
            const [k, ...rest] = line.split("=");
            if (k?.trim() && rest.length) env[k.trim()] = rest.join("=").trim();
          }
          payload.env = env;
        }
      }
      const result = await addMcpServer(payload);
      if (result.ok === false) { alert(result.message || "添加失败"); return; }
      onCreated();
      onClose();
    } catch (e) {
      alert(String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title="添加 MCP 服务" onClose={onClose}>
      <Field label="名称"><input value={name} onChange={e => setName(e.target.value)} className="input" placeholder="e.g. filesystem" /></Field>
      <Field label="传输方式">
        <select value={transport} onChange={e => setTransport(e.target.value as "http" | "stdio")} className="input">
          <option value="stdio">stdio (推荐，启动本地子进程)</option>
          <option value="http">HTTP (远程端点)</option>
        </select>
      </Field>
      {transport === "http" ? (
        <>
          <Field label="URL"><input value={url} onChange={e => setUrl(e.target.value)} className="input" placeholder="https://mcp.example.com" /></Field>
          <Field label="API Key (可选)"><input value={apiKey} onChange={e => setApiKey(e.target.value)} className="input" type="password" /></Field>
        </>
      ) : (
        <>
          <Field label="命令"><input value={command} onChange={e => setCommand(e.target.value)} className="input" placeholder="npx" /></Field>
          <Field label="参数 (空格分隔)"><input value={argsStr} onChange={e => setArgsStr(e.target.value)} className="input" placeholder="-y @modelcontextprotocol/server-filesystem /tmp" /></Field>
          <Field label="环境变量 (每行 KEY=VALUE)"><textarea value={envStr} onChange={e => setEnvStr(e.target.value)} className="input min-h-[80px]" placeholder="API_KEY=xxx" /></Field>
        </>
      )}
      <div className="flex gap-2 justify-end mt-4">
        <button onClick={onClose} className="btn-secondary">取消</button>
        <button onClick={submit} disabled={submitting} className="btn-primary">添加</button>
      </div>
    </Modal>
  );
}

// ============================================================================
// Providers Tab (Memory + Context Engine — Hermes single-select)
// ============================================================================
interface ProviderEntry {
  name: string;
  description?: string;
  module?: string;
  class?: string;
  active?: boolean;
  plugin_dir?: string;
}

function ProvidersTab() {
  const [memProviders, setMemProviders] = useState<ProviderEntry[]>([]);
  const [memActive, setMemActive] = useState<string | null>(null);
  const [ctxEngines, setCtxEngines] = useState<ProviderEntry[]>([]);
  const [ctxActive, setCtxActive] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [mp, ce] = await Promise.all([
      fetchMemoryProviders().catch(() => ({ providers: [], active: null })),
      fetchContextEngines().catch(() => ({ engines: [], active: null })),
    ]);
    setMemProviders(mp.providers || []);
    setMemActive(mp.active);
    setCtxEngines(ce.engines || []);
    setCtxActive(ce.active);
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="p-6 space-y-6">
      <section>
        <SectionHeader title="记忆 Provider (单选)" icon={Brain} />
        <div className="text-xs text-gray-400 mt-1 mb-3">
          激活的 Provider 会替换默认记忆实现。放到 <code>~/.hermes/plugins/&lt;name&gt;/memory_provider.json</code>
        </div>
        <div className="space-y-2">
          {memProviders.map(p => (
            <div key={p.name} className="card flex items-center gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-semibold">{p.name}</span>
                  {p.active && <span className="tag text-green-300">已激活</span>}
                </div>
                {p.description && <div className="text-xs text-gray-400 mt-1">{p.description}</div>}
                <div className="text-xs text-gray-600 mt-1 font-mono">{p.module}.{p.class}</div>
              </div>
              {p.active ? (
                <button onClick={async () => { await deactivateMemoryProvider(); load(); }} className="btn-secondary">停用</button>
              ) : (
                <button onClick={async () => { await activateMemoryProvider(p.name); load(); }} className="btn-primary">激活</button>
              )}
            </div>
          ))}
          {memProviders.length === 0 && <EmptyState text="尚未发现 Provider。创建 ~/.hermes/plugins/<name>/memory_provider.json" />}
        </div>
        {memActive && (
          <div className="text-xs text-gray-400 mt-2">当前激活: <code className="text-white">{memActive}</code></div>
        )}
      </section>

      <section>
        <SectionHeader title="上下文引擎 (单选)" icon={Brain} />
        <div className="text-xs text-gray-400 mt-1 mb-3">
          激活的 Engine 接管 prompt 拼装。放到 <code>~/.hermes/plugins/&lt;name&gt;/context_engine.json</code>
        </div>
        <div className="space-y-2">
          {ctxEngines.map(e => (
            <div key={e.name} className="card flex items-center gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-semibold">{e.name}</span>
                  {e.active && <span className="tag text-green-300">已激活</span>}
                </div>
                {e.description && <div className="text-xs text-gray-400 mt-1">{e.description}</div>}
                <div className="text-xs text-gray-600 mt-1 font-mono">{e.module}.{e.class}</div>
              </div>
              {e.active ? (
                <button onClick={async () => { await deactivateContextEngine(); load(); }} className="btn-secondary">停用</button>
              ) : (
                <button onClick={async () => { await activateContextEngine(e.name); load(); }} className="btn-primary">激活</button>
              )}
            </div>
          ))}
          {ctxEngines.length === 0 && <EmptyState text="尚未发现 Engine。创建 ~/.hermes/plugins/<name>/context_engine.json" />}
        </div>
        {ctxActive && (
          <div className="text-xs text-gray-400 mt-2">当前激活: <code className="text-white">{ctxActive}</code></div>
        )}
      </section>
    </div>
  );
}

// ============================================================================
// Scopes Tab (Managed / User / Project / Local permission layering)
// ============================================================================
interface ScopeDetail {
  scope: string;
  loaded: boolean;
  rules: {
    always_allow?: string[];
    always_deny?: string[];
    always_ask?: string[];
  };
}

function ScopesTab() {
  const [scopes, setScopes] = useState<ScopeDetail[]>([]);
  const [merged, setMerged] = useState<{ always_allow: string[]; always_deny: string[]; always_ask: string[] } | null>(null);
  const [reloading, setReloading] = useState(false);

  const load = useCallback(async () => {
    const data = await fetchPermissionScopes().catch(() => null);
    if (data) {
      setScopes(data.scopes || []);
      setMerged(data.merged || null);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function reload() {
    setReloading(true);
    try { await reloadPermissions(); await load(); }
    finally { setReloading(false); }
  }

  const scopeLabels: Record<string, { name: string; hint: string }> = {
    managed: { name: "托管层", hint: "/etc/hermes/ · 组织管控，不可被下层覆盖" },
    user: { name: "用户层", hint: "~/.hermes/ · 个人默认" },
    project: { name: "项目层", hint: "当前目录 .hermes/ · 团队共享，可提交 Git" },
    local: { name: "本地层", hint: ".hermes/permissions.local.json · 仓库内本地覆盖，不提交" },
  };

  return (
    <div className="p-6 grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div>
        <SectionHeader title="作用域层级" icon={Shield} action={
          <button onClick={reload} disabled={reloading} className="btn-secondary">
            <RefreshCw size={14} className={reloading ? "animate-spin" : ""} /> 重新加载
          </button>
        } />
        <div className="space-y-2 mt-3">
          {scopes.map(s => {
            const label = scopeLabels[s.scope];
            const totalRules = (s.rules.always_allow?.length || 0)
              + (s.rules.always_deny?.length || 0)
              + (s.rules.always_ask?.length || 0);
            return (
              <div key={s.scope} className="card">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-semibold">{label?.name || s.scope}</span>
                      {s.loaded ? <CheckCircle2 size={14} className="text-green-400" /> : <XCircle size={14} className="text-gray-500" />}
                    </div>
                    <div className="text-xs text-gray-400 mt-1">{label?.hint || ""}</div>
                  </div>
                  <div className="text-xs text-gray-500">{totalRules} 条</div>
                </div>
                {s.loaded && totalRules > 0 && (
                  <div className="mt-3 space-y-1 text-xs font-mono">
                    {(s.rules.always_deny || []).map((r, i) => (
                      <div key={"d" + i} className="text-red-300">拒绝  {r}</div>
                    ))}
                    {(s.rules.always_ask || []).map((r, i) => (
                      <div key={"a" + i} className="text-amber-300">询问  {r}</div>
                    ))}
                    {(s.rules.always_allow || []).map((r, i) => (
                      <div key={"l" + i} className="text-green-300">允许  {r}</div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div>
        <SectionHeader title="合并后 (生效规则)" icon={CheckCircle2} />
        {merged ? (
          <div className="card mt-3 font-mono text-xs space-y-1 max-h-[75vh] overflow-auto">
            {(merged.always_deny || []).map((r, i) => (
              <div key={"d" + i} className="text-red-300">拒绝  {r}</div>
            ))}
            {(merged.always_ask || []).map((r, i) => (
              <div key={"a" + i} className="text-amber-300">询问  {r}</div>
            ))}
            {(merged.always_allow || []).map((r, i) => (
              <div key={"l" + i} className="text-green-300">允许  {r}</div>
            ))}
            {!(merged.always_deny?.length || merged.always_ask?.length || merged.always_allow?.length) && (
              <div className="text-gray-500">没有规则</div>
            )}
          </div>
        ) : <EmptyState text="加载中..." />}
      </div>
    </div>
  );
}

// ============================================================================
// Shared Components
// ============================================================================
function SectionHeader({ title, icon: Icon, action }: { title: string; icon: LucideIcon; action?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <Icon size={16} />
      <h2 className="text-base font-semibold">{title}</h2>
      <div className="flex-1" />
      {action}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    completed: "bg-green-500/20 text-green-300",
    running: "bg-blue-500/20 text-blue-300",
    failed: "bg-red-500/20 text-red-300",
    pending: "bg-gray-500/20 text-gray-300",
    paused: "bg-amber-500/20 text-amber-300",
  };
  return <span className={`text-xs px-2 py-0.5 rounded ${colors[status] || "bg-gray-500/20 text-gray-300"}`}>{statusText(status)}</span>;
}

function EmptyState({ text }: { text: string }) {
  return <div className="text-sm text-gray-500 py-6 text-center">{text}</div>;
}

function Modal({ title, children, onClose, wide }: { title: string; children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div className={`rounded-lg shadow-xl ${wide ? "max-w-3xl" : "max-w-lg"} w-full max-h-[90vh] overflow-auto`}
           style={{ background: "var(--bg-primary)", border: "1px solid var(--border-color)" }}
           onClick={e => e.stopPropagation()}>
        <div className="px-5 py-4 border-b flex items-center justify-between" style={{ borderColor: "var(--border-color)" }}>
          <h3 className="font-semibold">{title}</h3>
          <button onClick={onClose} className="icon-btn">✕</button>
        </div>
        <div className="p-5 space-y-3">{children}</div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-gray-400 mb-1">{label}</label>
      {children}
    </div>
  );
}

function fmtTime(iso?: string): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = new Date();
    const diff = (now.getTime() - d.getTime()) / 1000;
    if (diff < 60) return `${Math.floor(diff)} 秒前`;
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
    return d.toLocaleString("zh-CN", { hour12: false });
  } catch { return iso; }
}

function sourceText(value: string): string {
  const map: Record<string, string> = {
    builtin: "内置",
    custom: "自定义",
    plugin: "插件",
  };
  return map[value] || value;
}

function statusText(value: string): string {
  const map: Record<string, string> = {
    completed: "已完成",
    running: "运行中",
    failed: "失败",
    pending: "等待中",
    paused: "已暂停",
  };
  return map[value] || value;
}

function decisionText(value: string): string {
  const map: Record<string, string> = {
    allow: "允许",
    deny: "拒绝",
    ask: "询问",
  };
  return map[value] || value;
}

function handlerTypeText(value: string): string {
  const map: Record<string, string> = {
    command: "命令",
    prompt: "提示词",
    agent: "子 Agent",
  };
  return map[value] || value;
}

function actionTypeText(value: string): string {
  const map: Record<string, string> = {
    command: "命令",
    prompt: "提示词",
    skill: "技能",
  };
  return map[value] || value;
}

function eventText(value: string): string {
  const map: Record<string, string> = {
    SessionStart: "会话开始",
    SessionEnd: "会话结束",
    UserPromptSubmit: "用户提交消息",
    PreToolUse: "工具调用前",
    PostToolUse: "工具调用后",
    Stop: "停止",
    SubagentStart: "子 Agent 启动",
    SubagentStop: "子 Agent 停止",
    PreCompact: "压缩前",
    PostCompact: "压缩后",
    Notification: "通知",
    FileEdit: "文件编辑",
    FileCreate: "文件创建",
    FileDelete: "文件删除",
    Error: "错误",
    RateLimitHit: "触发限流",
  };
  return map[value] || value;
}
