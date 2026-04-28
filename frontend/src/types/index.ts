export interface Message {
  id: string;
  thread_id: string;
  role: "user" | "assistant" | "system" | "agent";
  content: string;
  agent_id?: string;
  metadata?: Record<string, unknown>;
  created_at: string;
}

export interface Thread {
  id: string;
  title: string;
  messages: Message[];
  created_at: string;
  updated_at: string;
  metadata?: Record<string, unknown>;
}

export interface ThreadListItem {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message?: string;
}

export interface SkillConfig {
  name: string;
  display_name: string;
  description: string;
  enabled: boolean;
  built_in: boolean;
  system_prompt: string;
  tools: string[];
}

export interface ModelConfig {
  name: string;
  display_name: string;
  provider: string;
  api_key_env: string;
  base_url: string;
  model: string;
  max_tokens: number;
  supports_streaming: boolean;
  has_api_key?: boolean;
  api_key_source?: "credential_store" | "oauth" | "environment" | "settings" | "none";
}

export interface MemoryEntry {
  id: string;
  key: string;
  value: string;
  category: string;
  created_at: string;
  updated_at: string;
  access_count: number;
}

export interface DemoSeedAction {
  action: string;
  ok: boolean;
  target: string;
  message: string;
}

export interface DemoSeedResult {
  ok: boolean;
  dry_run: boolean;
  clean: boolean;
  summary: {
    total: number;
    passed: number;
    failed: number;
  };
  results: DemoSeedAction[];
}

export interface SecurityPolicyResponse {
  environment: string;
  production: boolean;
  production_policy_enabled: boolean;
  disabled_tools: string[];
  matrix: Record<string, string[]>;
}

export interface SecurityAuditEvent {
  event_id: string;
  timestamp: string;
  thread_id: string;
  agent_id: string;
  mode: string;
  tool: string;
  category: string;
  risk_level: string;
  decision: string;
  source: string;
  reason: string;
  matched_rule: string | null;
  input: string;
}

export interface SecurityAuditResponse {
  events: SecurityAuditEvent[];
}

export interface ToolMonitorEvent {
  event_id: string;
  timestamp: string;
  tool: string;
  category: string;
  thread_id: string;
  agent_id: string;
  mode: string;
  input: string;
  output: string;
  success: boolean;
  source: string;
  client_id: string;
}

export interface ToolMonitorResponse {
  events: ToolMonitorEvent[];
  threads?: {
    thread_id: string;
    count: number;
    latest_at: string;
    latest_tool: string;
  }[];
}

export interface ChatRequest {
  thread_id?: string | null;
  message: string;
  model?: string | null;
  skills?: string[] | null;
  tools?: string[] | null;
  disabled_tools?: string[] | null;
  mode?: string | null;
  images?: string[] | null;
  enable_speculation?: boolean | null;
}

export interface SpeculationChange {
  path: string;
  status: string;
  size?: number;
  conflict?: boolean;
}

export interface SpeculationHunkLine {
  type: "context" | "add" | "del";
  content: string;
}

export interface SpeculationHunk {
  id: string;
  header: string;
  old_start: number;
  old_count: number;
  new_start: number;
  new_count: number;
  diff: string;
  lines: SpeculationHunkLine[];
  truncated: boolean;
}

export interface SpeculationDiffEntry extends SpeculationChange {
  diff: string;
  binary: boolean;
  truncated: boolean;
  hunks: SpeculationHunk[];
}

export interface SpeculationHunkSelection {
  path: string;
  ids: string[];
}

export interface SpeculationAppliedChange {
  path: string;
  status: string;
  mode?: string;
  hunks?: string[];
}

export interface SpeculationAcceptResult {
  status?: string;
  error?: string;
  applied?: SpeculationAppliedChange[];
  accepted_all?: boolean;
  remaining_changes?: SpeculationChange[];
  remaining_conflicts?: SpeculationChange[];
}

export interface SpeculationNotice {
  kind: "success" | "error" | "info";
  title: string;
  detail?: string;
  applied?: SpeculationAppliedChange[];
  remainingCount?: number;
}

export interface SpeculationToolCall {
  tool: string;
  input?: string;
  category?: string;
}

export interface SpeculationPreview {
  suggestion?: string;
  preview?: string[];
}

export interface SpeculationRecord {
  thread_id: string;
  shadow_thread_id?: string;
  suggestion: string;
  preview?: SpeculationPreview | null;
  created_at: string;
  status: string;
  draft: string;
  error: string;
  source: string;
  execution_mode: string;
  tool_summary: string;
  tool_calls: SpeculationToolCall[];
  changes: SpeculationChange[];
  accepted_at: string;
  consumed_at: string;
}

export interface PromptSuggestionEventData {
  suggestion?: string;
  speculation?: SpeculationPreview | null;
  background?: SpeculationRecord | null;
}

export interface SSEEventData {
  type:
    | "token"
    | "error"
    | "done"
    | "plan"
    | "agent_status"
    | "agent_summary"
    | "tool_call"
    | "tool_result"
    | "validation_result"
    | "tool_summary"
    | "permission_request"
    | "permission_decision"
    | "prompt_suggestion"
    | "speculation_state"
    | "speculation_hit"
    | "agents_completed"
    | "stream_output"
    | "file_diff"
    | "files_changed";
  content?: string;
  thread_id?: string;
  steps?: string[];
  agent_id?: string;
  status?: string;
  task?: string;
  data?: Record<string, unknown>;
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
    cost_usd?: number;
    tool_calls?: number;
    agents_spawned?: number;
  };
}

export interface WorkspaceFile {
  name: string;
  is_dir: boolean;
  size: number;
}

export interface WorkspaceListing {
  path: string;
  entries: WorkspaceFile[];
}

export interface LocalClient {
  client_id: string;
  info: {
    hostname?: string;
    os?: string;
    os_version?: string;
    arch?: string;
    python?: string;
    home?: string;
  };
  connected_at: number;
  auto_approve: boolean;
  tool_auto_approve: string[];
}
