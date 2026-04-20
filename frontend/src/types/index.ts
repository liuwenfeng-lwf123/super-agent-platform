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

export interface ChatRequest {
  thread_id?: string | null;
  message: string;
  model?: string | null;
  skills?: string[] | null;
  mode?: string | null;
}

export interface SSEEventData {
  type: "token" | "error" | "done" | "plan" | "agent_status" | "tool_call" | "tool_result";
  content?: string;
  thread_id?: string;
  steps?: string[];
  agent_id?: string;
  status?: string;
  task?: string;
  data?: Record<string, unknown>;
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
}
