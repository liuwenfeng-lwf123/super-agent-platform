import type {
  ChatRequest,
  DemoSeedResult,
  SecurityAuditResponse,
  SecurityPolicyResponse,
  ToolMonitorResponse,
  SSEEventData,
  SpeculationHunkSelection,
} from "@/types";

const API_BASE = "/api";

/** Read optional API token from env (set NEXT_PUBLIC_API_TOKEN in .env.local). */
function getAuthHeaders(): Record<string, string> {
  const token = typeof process !== "undefined"
    ? process.env.NEXT_PUBLIC_API_TOKEN
    : undefined;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function safeFetch(input: RequestInfo | URL, init?: RequestInit) {
  const headers = { ...getAuthHeaders(), ...init?.headers };
  const res = await fetch(input, { ...init, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body?.detail || body?.error || `HTTP ${res.status}`);
  }
  return res;
}

async function streamSSE(
  url: string,
  body: object,
  onEvent: (event: SSEEventData) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    throw new Error(`HTTP error: ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "";

  const IDLE_TIMEOUT_MS = 120_000; // 2 min with no data → assume stuck
  let idleTimer: ReturnType<typeof setTimeout> | null = null;
  const resetIdle = () => {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      // Force-fire a synthetic done so the UI never stays stuck
      onEvent({ type: "done" } as SSEEventData);
      try { reader.cancel(); } catch { /* ignore */ }
    }, IDLE_TIMEOUT_MS);
  };

  try {
    resetIdle();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      resetIdle();

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.slice(6));
            onEvent({ type: currentEvent as SSEEventData["type"], ...data });
            if (data?.type === "done") {
              return;
            }
          } catch {
            // skip malformed data
          }
        }
      }
    }
  } finally {
    if (idleTimer) clearTimeout(idleTimer);
    reader.releaseLock();
  }
}

export async function sendMessage(
  request: ChatRequest,
  onEvent: (event: SSEEventData) => void,
  onError?: (error: Error) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamSSE(`${API_BASE}/chat`, request, onEvent, signal);
}

export async function approvePermissionRequest(requestId: string, note: string = "") {
  const params = note ? `?note=${encodeURIComponent(note)}` : "";
  const res = await fetch(`${API_BASE}/permissions/${encodeURIComponent(requestId)}/approve${params}`, { method: "POST" });
  return res.json();
}

export async function denyPermissionRequest(requestId: string, note: string = "") {
  const params = note ? `?note=${encodeURIComponent(note)}` : "";
  const res = await fetch(`${API_BASE}/permissions/${encodeURIComponent(requestId)}/deny${params}`, { method: "POST" });
  return res.json();
}

export async function fetchPendingPermissions(threadId?: string) {
  const params = threadId ? `?thread_id=${encodeURIComponent(threadId)}` : "";
  const res = await fetch(`${API_BASE}/permissions/pending${params}`);
  return res.json();
}

export async function fetchThreads() {
  const res = await safeFetch(`${API_BASE}/threads`);
  return res.json();
}

export async function fetchThread(threadId: string) {
  const res = await safeFetch(`${API_BASE}/threads/${threadId}`);
  return res.json();
}

export async function deleteThread(threadId: string) {
  const res = await safeFetch(`${API_BASE}/threads/${threadId}`, { method: "DELETE" });
  return res.json();
}

export async function fetchModels() {
  const res = await safeFetch(`${API_BASE}/models`);
  return res.json();
}

export async function saveProviderApiKey(provider: string, apiKey: string, label: string = "default") {
  const res = await safeFetch(`${API_BASE}/credentials/keys/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, api_key: apiKey, label }),
  });
  return res.json();
}

export async function fetchProviderKeys(provider: string) {
  const res = await fetch(`${API_BASE}/credentials/keys/${encodeURIComponent(provider)}`);
  return res.json();
}

export async function deleteProviderApiKey(provider: string, label: string) {
  const res = await fetch(`${API_BASE}/credentials/keys/${encodeURIComponent(provider)}/${encodeURIComponent(label)}`, {
    method: "DELETE",
  });
  return res.json();
}

export async function fetchSkills() {
  const res = await safeFetch(`${API_BASE}/skills`);
  return res.json();
}

export async function fetchMemory() {
  const res = await fetch(`${API_BASE}/memory`);
  return res.json();
}

export async function addMemory(key: string, value: string, category: string) {
  const res = await fetch(
    `${API_BASE}/memory?key=${encodeURIComponent(key)}&value=${encodeURIComponent(value)}&category=${encodeURIComponent(category)}`,
    { method: "POST" }
  );
  return res.json();
}

export async function deleteMemory(entryId: string) {
  const res = await safeFetch(`${API_BASE}/memory/${entryId}`, { method: "DELETE" });
  return res.json();
}

export async function seedDemoData(options: { clean?: boolean; dryRun?: boolean } = {}): Promise<DemoSeedResult> {
  const params = new URLSearchParams({
    clean: String(options.clean ?? true),
    dry_run: String(options.dryRun ?? false),
  });
  const res = await fetch(`${API_BASE}/demo/seed?${params}`, { method: "POST" });
  return res.json();
}

export async function fetchSecurityPolicy(): Promise<SecurityPolicyResponse> {
  const res = await fetch(`${API_BASE}/security/policy`);
  return res.json();
}

export async function fetchSecurityAudit(options: { limit?: number; tool?: string; decision?: string; threadId?: string } = {}): Promise<SecurityAuditResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(options.limit ?? 50));
  if (options.tool) params.set("tool", options.tool);
  if (options.decision) params.set("decision", options.decision);
  if (options.threadId) params.set("thread_id", options.threadId);
  const res = await fetch(`${API_BASE}/security/audit?${params}`);
  return res.json();
}

export interface FeatureInfo {
  id: string;
  name: string;
  description: string;
  status: "active" | "available" | "unavailable";
  category: string;
  how_to_use: string;
  toggleable?: boolean;
  config?: Record<string, unknown>;
}

export async function fetchFeatures(): Promise<{ features: FeatureInfo[] }> {
  const res = await fetch(`${API_BASE}/features`);
  return res.json();
}

export async function toggleFeature(featureId: string, enable: boolean): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${API_BASE}/features/toggle`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ feature_id: featureId, enable }),
  });
  return res.json();
}

// Token Budget Control
export interface TokenFeature {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  est_tokens_per_use: number;
}

export interface TokenBudgetResponse {
  features: TokenFeature[];
  total_est_tokens: number;
  active_est_tokens: number;
  saved_tokens: number;
}

export async function fetchTokenBudget(): Promise<TokenBudgetResponse> {
  const res = await fetch(`${API_BASE}/features/token-budget`);
  return res.json();
}

export async function toggleTokenFeature(featureId: string, enable: boolean): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${API_BASE}/features/token-budget/toggle`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ feature_id: featureId, enable }),
  });
  return res.json();
}

// Preset modes
export interface PresetInfo {
  name: string;
  description: string;
  est_per_chat: string;
  features: Record<string, boolean>;
}

export async function fetchTokenPresets(): Promise<{ presets: Record<string, PresetInfo>; active_preset: string | null }> {
  const res = await fetch(`${API_BASE}/features/token-budget/presets`);
  return res.json();
}

export async function applyTokenPreset(presetName: string): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${API_BASE}/features/token-budget/preset/${presetName}`, { method: "POST" });
  return res.json();
}

// Daily budget
export interface DailyUsage {
  date: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  budget: number;
  budget_used_pct: number;
  is_over_budget: boolean;
  remaining: number;
}

export async function fetchDailyUsage(): Promise<DailyUsage> {
  const res = await fetch(`${API_BASE}/features/token-budget/daily`);
  return res.json();
}

export async function setDailyBudget(budget: number): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${API_BASE}/features/token-budget/daily`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ daily_token_budget: budget }),
  });
  return res.json();
}

// Token usage history
export interface DayUsage {
  date: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  requests: number;
}

export async function fetchTokenHistory(days: number = 7): Promise<{ days: DayUsage[] }> {
  const res = await fetch(`${API_BASE}/features/token-budget/history?days=${days}`);
  return res.json();
}

// Cache stats
export interface CacheStats {
  total_input_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  total_cache_tokens: number;
  cache_hit_rate: number;
  saved_usd: number;
  total_cost_usd: number;
  records_with_cache: number;
  total_records: number;
}

// Model breakdown
export interface ModelBreakdown {
  model: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
}

export async function fetchModelBreakdown(): Promise<{ models: ModelBreakdown[] }> {
  const res = await fetch(`${API_BASE}/features/token-budget/model-breakdown`);
  return res.json();
}

export async function fetchCacheStats(): Promise<CacheStats> {
  const res = await fetch(`${API_BASE}/features/token-budget/cache-stats`);
  return res.json();
}

export function getExportCsvUrl(month?: string): string {
  const m = month || new Date().toISOString().slice(0, 7);
  return `${API_BASE}/features/token-budget/export?month=${m}`;
}

export async function fetchWorkspaceFiles(threadId: string, path: string = ".") {
  const res = await fetch(`${API_BASE}/workspace/${threadId}/files?path=${encodeURIComponent(path)}`);
  return res.json();
}

export async function fetchSpeculation(threadId: string) {
  const res = await fetch(`${API_BASE}/speculation/${threadId}`);
  return res.json();
}

export async function fetchSpeculationChanges(threadId: string) {
  const res = await fetch(`${API_BASE}/speculation/${threadId}/changes`);
  return res.json();
}

export async function fetchSpeculationDiff(threadId: string) {
  const res = await fetch(`${API_BASE}/speculation/${threadId}/diff`);
  return res.json();
}

export async function acceptSpeculation(threadId: string, paths?: string[], hunks?: SpeculationHunkSelection[]) {
  const res = await fetch(`${API_BASE}/speculation/${threadId}/accept`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...(paths && paths.length > 0 ? { paths } : {}),
      ...(hunks && hunks.length > 0 ? { hunks } : {}),
    }),
  });
  return res.json();
}

export async function clearSpeculation(threadId: string) {
  const res = await fetch(`${API_BASE}/speculation/${threadId}`, { method: "DELETE" });
  return res.json();
}

export function getWorkspaceDownloadUrl(threadId: string, filePath: string) {
  return `${API_BASE}/workspace/${threadId}/download/${filePath}`;
}

export function getOutputDownloadUrl(threadId: string, filePath: string) {
  return `${API_BASE}/outputs/${threadId}/download/${filePath}`;
}

export async function fetchLocalClients() {
  const res = await fetch(`${API_BASE}/local/clients`);
  return res.json();
}

export async function setLocalAutoApprove(clientId: string, enabled: boolean) {
  const res = await fetch(`${API_BASE}/local/clients/${clientId}/auto-approve?enabled=${enabled}`, { method: "POST" });
  return res.json();
}

export async function setLocalToolPermission(clientId: string, tool: string, enabled: boolean) {
  const res = await fetch(`${API_BASE}/local/clients/${clientId}/tool-permission?tool=${encodeURIComponent(tool)}&enabled=${enabled}`, { method: "POST" });
  return res.json();
}

export async function bindLocalThread(threadId: string, clientId: string) {
  const res = await fetch(`${API_BASE}/local/bind-thread?thread_id=${threadId}&client_id=${clientId}`, { method: "POST" });
  return res.json();
}

export async function fetchLocalAuditLog(limit: number = 100) {
  const res = await fetch(`${API_BASE}/local/audit?limit=${limit}`);
  return res.json();
}

export async function fetchLocalToolStats(): Promise<{ tool: string; total: number; success: number }[]> {
  const res = await fetch(`${API_BASE}/local/tool-stats`);
  return res.json();
}

// --- Shortcuts ---
export async function fetchLocalShortcuts(): Promise<{ name: string; description: string; steps: string[] }[]> {
  const res = await fetch(`${API_BASE}/local/shortcuts`);
  return res.json();
}

export async function createLocalShortcut(name: string, description: string, steps: string[]) {
  const res = await fetch(`${API_BASE}/local/shortcuts`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, description, steps }) });
  return res.json();
}

export async function deleteLocalShortcut(name: string) {
  const res = await fetch(`${API_BASE}/local/shortcuts/${encodeURIComponent(name)}`, { method: "DELETE" });
  return res.json();
}

// --- Schedules ---
export interface LocalSchedule {
  id: string;
  message: string;
  description: string;
  thread_id: string;
  run_at: string | null;
  interval_minutes: number | null;
  created_at: string;
  last_run: string | null;
  enabled: boolean;
}

export async function fetchLocalSchedules(): Promise<LocalSchedule[]> {
  const res = await fetch(`${API_BASE}/local/schedules`);
  return res.json();
}

export async function createLocalSchedule(data: { message: string; description?: string; run_at?: string; interval_minutes?: number; thread_id?: string }) {
  const res = await fetch(`${API_BASE}/local/schedules`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  return res.json();
}

export async function deleteLocalSchedule(id: string) {
  const res = await fetch(`${API_BASE}/local/schedules/${encodeURIComponent(id)}`, { method: "DELETE" });
  return res.json();
}

export async function sendLocalMessage(
  request: { thread_id?: string; message: string; model?: string; mode?: string },
  onEvent: (event: SSEEventData) => void,
  onError?: (error: Error) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamSSE(`${API_BASE}/local/chat`, request, onEvent, signal);
}

// --- Cost & Usage ---
export async function fetchCostStats() {
  const res = await fetch(`${API_BASE}/cost`);
  return res.json();
}

export async function fetchStatusline() {
  const res = await fetch(`${API_BASE}/statusline`);
  return res.json();
}

export async function fetchMcpPrompts() {
  const res = await fetch(`${API_BASE}/mcp/prompts`);
  return res.json();
}

export async function callMcpPrompt(serverName: string, promptName: string, argumentsObj: Record<string, unknown> = {}) {
  const res = await fetch(`${API_BASE}/mcp/prompts/call`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ server_name: serverName, prompt_name: promptName, arguments: argumentsObj }),
  });
  return res.json();
}

export async function fetchMcpResources() {
  const res = await fetch(`${API_BASE}/mcp/resources`);
  return res.json();
}

export async function readMcpResource(serverName: string, uri: string) {
  const res = await fetch(`${API_BASE}/mcp/resources/read`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ server_name: serverName, uri }),
  });
  return res.json();
}

export async function fetchCostHistory() {
  const res = await fetch(`${API_BASE}/cost/history`);
  return res.json();
}

export async function fetchModelUsage() {
  const res = await fetch(`${API_BASE}/cost/models`);
  return res.json();
}

// --- Layered Memory ---
export async function fetchMemoryStats() {
  const res = await fetch(`${API_BASE}/memory/stats`);
  return res.json();
}

export async function fetchLayeredMemory() {
  const res = await fetch(`${API_BASE}/memory/layered`);
  return res.json();
}

export async function fetchProjectMemory() {
  const res = await fetch(`${API_BASE}/memory/project`);
  return res.json();
}

export async function updateProjectMemory(content: string) {
  const res = await fetch(`${API_BASE}/memory/project?content=${encodeURIComponent(content)}`, { method: "POST" });
  return res.json();
}

export async function searchMemory(query: string) {
  const res = await fetch(`${API_BASE}/memory/search?query=${encodeURIComponent(query)}`);
  return res.json();
}

// --- File History ---
export async function fetchFileHistory(threadId: string, path?: string, limit?: number) {
  let url = `${API_BASE}/threads/${threadId}/file-history?limit=${limit || 50}`;
  if (path) url += `&path=${encodeURIComponent(path)}`;
  const res = await fetch(url);
  return res.json();
}

// --- Compact State ---
export async function fetchCompactState() {
  const res = await fetch(`${API_BASE}/context/compact`);
  return res.json();
}

// --- Evolution / Skills ---
export async function fetchSkillsList() {
  const res = await fetch(`${API_BASE}/skills`);
  return res.json();
}

export async function fetchSkillLibrary() {
  const res = await fetch(`${API_BASE}/skill-library`);
  return res.json();
}

export async function installSkillFromLibrary(name: string, force: boolean = false) {
  const res = await fetch(`${API_BASE}/skill-library/${encodeURIComponent(name)}/install`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
  });
  return res.json();
}

export async function previewOpenClawSkill(payload: { content?: string; url?: string }) {
  const res = await fetch(`${API_BASE}/skill-library/openclaw/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function installOpenClawSkill(payload: { content?: string; url?: string; force?: boolean }) {
  const res = await fetch(`${API_BASE}/skill-library/openclaw/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function searchClawHubSkills(query: string, limit: number = 20) {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  const res = await fetch(`${API_BASE}/skill-library/clawhub/search?${params}`);
  return res.json();
}

export async function fetchClawHubAuthStatus() {
  const res = await fetch(`${API_BASE}/skill-library/clawhub/auth`);
  return res.json();
}

export async function previewClawHubSkill(slug: string, version?: string) {
  const res = await fetch(`${API_BASE}/skill-library/clawhub/${encodeURIComponent(slug)}/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version }),
  });
  return res.json();
}

export async function installClawHubSkill(slug: string, options: { version?: string; force?: boolean } = {}) {
  const res = await fetch(`${API_BASE}/skill-library/clawhub/${encodeURIComponent(slug)}/install`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options),
  });
  return res.json();
}

export async function fetchSkillDetail(name: string) {
  const res = await fetch(`${API_BASE}/skills/${name}`);
  return res.json();
}

export async function fetchSkillVersions(name: string) {
  const res = await fetch(`${API_BASE}/skills/${name}/versions`);
  return res.json();
}

export async function patchSkill(name: string, oldString: string, newString: string) {
  const res = await fetch(`${API_BASE}/skills/${name}/patch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ old_string: oldString, new_string: newString }),
  });
  return res.json();
}

export async function editSkill(name: string, content: string) {
  const res = await fetch(`${API_BASE}/skills/${name}/edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  return res.json();
}

export async function rollbackSkill(name: string) {
  const res = await fetch(`${API_BASE}/skills/${name}/rollback`, { method: "POST" });
  return res.json();
}

export async function copySkill(name: string, newName?: string, displayName?: string) {
  const res = await fetch(`${API_BASE}/skills/${name}/copy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_name: newName, display_name: displayName }),
  });
  return res.json();
}

export async function evolveSkill(skillName: string, skillContent: string, iterations: number = 2) {
  const res = await fetch(`${API_BASE}/evolution/evolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ skill_name: skillName, skill_content: skillContent, iterations }),
  });
  return res.json();
}

export async function applyEvolutionCandidate(skillName: string) {
  const res = await fetch(`${API_BASE}/evolution/apply-candidate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ skill_name: skillName }),
  });
  return res.json();
}

export async function fetchEvolutionLog() {
  const res = await fetch(`${API_BASE}/evolution/log`);
  return res.json();
}

export async function fetchEvolutionStats() {
  const res = await fetch(`${API_BASE}/evolution/stats`);
  return res.json();
}

export async function fetchCustomTools() {
  const res = await fetch(`${API_BASE}/evolution/tools`);
  return res.json();
}

// --------------------------------------------------------------------
// Hermes: Subagents
// --------------------------------------------------------------------
export async function fetchSubagents() {
  const res = await fetch(`${API_BASE}/subagents`);
  return res.json();
}

export async function createSubagent(payload: {
  name: string; description: string; prompt: string;
  tools?: string[]; model?: string; max_turns?: number;
  isolation?: string; background?: boolean;
}) {
  const res = await fetch(`${API_BASE}/subagents/create`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function deleteSubagent(name: string) {
  const res = await fetch(`${API_BASE}/subagents/${name}`, { method: "DELETE" });
  return res.json();
}

export async function spawnSubagent(payload: {
  agent_name: string; task_prompt: string; background?: boolean;
}) {
  const res = await fetch(`${API_BASE}/subagents/spawn`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function fetchSubagentInstances() {
  const res = await fetch(`${API_BASE}/subagents/instances`);
  return res.json();
}

export async function fetchSubagentInstance(agentId: string) {
  const res = await fetch(`${API_BASE}/subagents/instance/${agentId}`);
  return res.json();
}

export async function sendSubagentMessage(agentId: string, message: string) {
  const res = await fetch(`${API_BASE}/subagents/${agentId}/message`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  return res.json();
}

export async function cleanupSubagentWorktree(agentId: string, removeBranch = false) {
  const res = await fetch(`${API_BASE}/subagents/instance/${agentId}/cleanup-worktree`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ remove_branch: removeBranch }),
  });
  return res.json();
}

// --------------------------------------------------------------------
// Hermes: Hooks
// --------------------------------------------------------------------
export async function fetchHooks() {
  const res = await fetch(`${API_BASE}/hooks`);
  return res.json();
}

export async function registerHook(payload: {
  event: string; name: string; description?: string;
  handlers: Array<{ handler_type: string; command?: string; prompt?: string; agent?: string }>;
  matcher?: string;
}) {
  const res = await fetch(`${API_BASE}/hooks/register`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function deleteHook(name: string) {
  const res = await fetch(`${API_BASE}/hooks/${name}`, { method: "DELETE" });
  return res.json();
}

export async function enableHook(name: string) {
  const res = await fetch(`${API_BASE}/hooks/${name}/enable`, { method: "POST" });
  return res.json();
}

export async function disableHook(name: string) {
  const res = await fetch(`${API_BASE}/hooks/${name}/disable`, { method: "POST" });
  return res.json();
}

export async function fireHook(event: string, context: Record<string, unknown>) {
  const res = await fetch(`${API_BASE}/hooks/fire`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event, context }),
  });
  return res.json();
}

export async function fetchHookHistory(limit = 50) {
  const res = await fetch(`${API_BASE}/hooks/history?limit=${limit}`);
  return res.json();
}

// --------------------------------------------------------------------
// Hermes: Cron
// --------------------------------------------------------------------
export async function fetchCronJobs() {
  const res = await fetch(`${API_BASE}/cron`);
  return res.json();
}

export async function addCronJob(payload: {
  name: string; schedule: string; action: string;
  action_type: string; enabled?: boolean;
}) {
  const res = await fetch(`${API_BASE}/cron`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function deleteCronJob(name: string) {
  const res = await fetch(`${API_BASE}/cron/${name}`, { method: "DELETE" });
  return res.json();
}

export async function runCronJob(name: string) {
  const res = await fetch(`${API_BASE}/cron/${name}/run`, { method: "POST" });
  return res.json();
}

export async function enableCronJob(name: string) {
  const res = await fetch(`${API_BASE}/cron/${name}/enable`, { method: "POST" });
  return res.json();
}

export async function disableCronJob(name: string) {
  const res = await fetch(`${API_BASE}/cron/${name}/disable`, { method: "POST" });
  return res.json();
}

// --------------------------------------------------------------------
// Hermes: Plugins
// --------------------------------------------------------------------
export async function fetchPlugins() {
  const res = await fetch(`${API_BASE}/plugins`);
  return res.json();
}

export async function enablePlugin(name: string) {
  const res = await fetch(`${API_BASE}/plugins/${name}/enable`, { method: "POST" });
  return res.json();
}

export async function disablePlugin(name: string) {
  const res = await fetch(`${API_BASE}/plugins/${name}/disable`, { method: "POST" });
  return res.json();
}

export async function discoverPlugins() {
  const res = await fetch(`${API_BASE}/plugins/discover`, { method: "POST" });
  return res.json();
}

// --------------------------------------------------------------------
// Hermes: GEPA (backend path: /api/evolution/gepa)
// --------------------------------------------------------------------
export async function gepaEvolve(payload: {
  initial_prompt: string; num_generations?: number;
  population_size?: number; use_llm?: boolean;
  eval_cases?: unknown[];
}) {
  // Map frontend names to backend param names
  const body = {
    original: payload.initial_prompt,
    eval_cases: payload.eval_cases || [],
    population_size: payload.population_size ?? 6,
    generations: payload.num_generations ?? 5,
  };
  const res = await fetch(`${API_BASE}/evolution/gepa`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  // Normalize backend response keys to match frontend expectation
  return {
    best_score: data.best_score,
    best_prompt: data.best_content_preview,
    baseline_score: data.baseline_score,
    improvement: data.improvement,
    generations: Array.isArray(data.generations)
      ? data.generations
      : typeof data.generations === "number"
        ? Array.from({ length: data.generations }, (_, i) => ({
            generation: i + 1,
            best_score: 0,
            avg_score: 0,
          }))
        : [],
  };
}

export async function fetchGepaHistory() {
  const res = await fetch(`${API_BASE}/evolution/log`);
  return res.json();
}

// --------------------------------------------------------------------
// Hermes: MCP Servers
// --------------------------------------------------------------------
export async function fetchMcpServers() {
  const res = await fetch(`${API_BASE}/mcp/servers`);
  return res.json();
}

export async function addMcpServer(payload: {
  name: string;
  transport?: "http" | "stdio";
  url?: string;
  api_key?: string | null;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  enabled?: boolean;
}) {
  const res = await fetch(`${API_BASE}/mcp/servers`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function removeMcpServer(name: string) {
  const res = await fetch(`${API_BASE}/mcp/servers/${encodeURIComponent(name)}`, { method: "DELETE" });
  return res.json();
}

export async function fetchMcpTools() {
  const res = await fetch(`${API_BASE}/mcp/tools`);
  return res.json();
}

export async function discoverMcpTools() {
  const res = await fetch(`${API_BASE}/mcp/discover`, { method: "POST" });
  return res.json();
}

// --------------------------------------------------------------------
// Hermes: Memory Provider / Context Engine
// --------------------------------------------------------------------
export async function fetchMemoryProviders() {
  const res = await fetch(`${API_BASE}/providers/memory`);
  return res.json();
}

export async function activateMemoryProvider(name: string) {
  const res = await fetch(`${API_BASE}/providers/memory/activate`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  return res.json();
}

export async function deactivateMemoryProvider() {
  const res = await fetch(`${API_BASE}/providers/memory/deactivate`, { method: "POST" });
  return res.json();
}

export async function fetchContextEngines() {
  const res = await fetch(`${API_BASE}/providers/context-engine`);
  return res.json();
}

export async function activateContextEngine(name: string) {
  const res = await fetch(`${API_BASE}/providers/context-engine/activate`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  return res.json();
}

export async function deactivateContextEngine() {
  const res = await fetch(`${API_BASE}/providers/context-engine/deactivate`, { method: "POST" });
  return res.json();
}

// --------------------------------------------------------------------
// Hermes: Permission scopes
// --------------------------------------------------------------------
export async function fetchPermissionScopes() {
  const res = await fetch(`${API_BASE}/permissions/scopes`);
  return res.json();
}

export async function reloadPermissions() {
  const res = await fetch(`${API_BASE}/permissions/reload`, { method: "POST" });
  return res.json();
}

// --------------------------------------------------------------------
// Hermes: Session search (FTS5)
// --------------------------------------------------------------------
export async function searchSessions(query: string, limit = 20) {
  const res = await fetch(`${API_BASE}/sessions/search?q=${encodeURIComponent(query)}&limit=${limit}`);
  return res.json();
}

export async function fetchSessionSearchStats() {
  const res = await fetch(`${API_BASE}/sessions/stats`);
  return res.json();
}

export async function rebuildSessionSearch() {
  const res = await fetch(`${API_BASE}/sessions/search/rebuild`, { method: "POST" });
  return res.json();
}

export async function fetchToolMonitorEvents(limit = 100, source = "", threadId = ""): Promise<ToolMonitorResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (source) params.set("source", source);
  if (threadId) params.set("thread_id", threadId);
  const res = await fetch(`${API_BASE}/monitor/tool-events?${params}`);
  return res.json();
}
