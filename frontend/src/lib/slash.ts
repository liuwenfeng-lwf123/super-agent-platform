// Slash-command parser & dispatcher (Claude Code pattern).
// Intercepts inputs starting with "/" and routes to dedicated backend APIs
// instead of the chat LLM. This gives users a power-user control channel
// without burning tokens.

import {
  callMcpPrompt,
  fetchCostStats,
  fetchCompactState,
  fetchModels,
  fetchStatusline,
} from "@/lib/api";

export interface SlashCommand {
  name: string;
  description: string;
  handler: (args: string) => Promise<string>;
  aliases?: string[];
}

async function runCompact(_args: string): Promise<string> {
  try {
    const state = await fetchCompactState();
    const level = state?.level || "none";
    const pct = state?.token_usage_pct ? ` (${state.token_usage_pct}% tokens)` : "";
    return `**Compact state**: \`${level}\`${pct}\n\n` +
      "Context compression runs automatically when the conversation exceeds token thresholds. " +
      "Levels: `none` → `soft` (summarize middle) → `hard` (aggressive compaction).";
  } catch (e) {
    return `Failed to fetch compact state: ${e}`;
  }
}

async function runStatusline(_args: string): Promise<string> {
  try {
    const s = await fetchStatusline();
    const budget = typeof s?.budget_usd === "number"
      ? `$${s.budget_usd.toFixed(2)}${s.is_over_budget ? " (EXCEEDED)" : ""}`
      : "unset";
    return [
      "## Statusline",
      "",
      `- **Threads**: ${s?.threads ?? 0}`,
      `- **Models**: ${s?.models ?? 0}`,
      `- **MCP servers**: ${s?.mcp_servers ?? 0}`,
      `- **Compact**: ${s?.compact_level ?? "none"} (${s?.token_usage_pct ?? 0}% tokens)`,
      `- **Requests**: ${s?.session_requests ?? 0}`,
      `- **Cost**: $${typeof s?.total_cost_usd === "number" ? s.total_cost_usd.toFixed(4) : "0.0000"}`,
      `- **Budget**: ${budget}`,
    ].join("\n");
  } catch (e) {
    return `Failed to fetch statusline: ${e}`;
  }
}

async function runTasks(_args: string): Promise<string> {
  try {
    const res = await fetch("/api/tasks");
    const data = await res.json();
    const tasks = Array.isArray(data?.tasks) ? data.tasks : Array.isArray(data) ? data : [];
    if (!tasks.length) return "_No tasks yet. Tasks are created from user messages and tracked automatically._";
    const lines = tasks.slice(0, 15).map((t: Record<string, unknown>) => {
      const status = String(t.status || "pending");
      const title = String(t.title || t.prompt || t.id || "untitled").slice(0, 80);
      const icon = status === "completed" ? "✓" : status === "running" ? "…" : "○";
      return `${icon} **${status}** — ${title}`;
    });
    return "## Recent Tasks\n\n" + lines.join("\n");
  } catch (e) {
    return `Failed to fetch tasks: ${e}`;
  }
}

async function runUsage(_args: string): Promise<string> {
  try {
    const s = await fetchCostStats();
    if (!s) return "Cost tracking not available.";
    const cost = typeof s.total_cost_usd === "number" ? s.total_cost_usd.toFixed(4) : "?";
    const input = s.total_input_tokens ?? 0;
    const output = s.total_output_tokens ?? 0;
    return [
      "## Session Usage",
      "",
      `- **Requests**: ${s.session_requests ?? 0}`,
      `- **Input tokens**: ${input.toLocaleString()}`,
      `- **Output tokens**: ${output.toLocaleString()}`,
      `- **Cost**: $${cost}`,
      s.budget_usd ? `- **Budget**: $${s.budget_usd.toFixed(2)} (${s.is_over_budget ? "EXCEEDED" : "ok"})` : "",
    ].filter(Boolean).join("\n");
  } catch (e) {
    return `Failed to fetch usage: ${e}`;
  }
}

async function runModels(_args: string): Promise<string> {
  try {
    const data = await fetchModels();
    const models = Array.isArray(data?.models) ? data.models : [];
    if (!models.length) return "_No models registered._";
    const lines = models.map((m: Record<string, unknown>) =>
      `- **${m.display_name || m.name}** (\`${m.name}\`)`,
    );
    return "## Available Models\n\n" + lines.join("\n") + "\n\n" +
      "_Switch models via the dropdown in the header._";
  } catch (e) {
    return `Failed to fetch models: ${e}`;
  }
}

async function runSearch(query: string): Promise<string> {
  if (!query.trim()) return "Usage: `/search <query>` — search across all past conversations.";
  try {
    const res = await fetch(`/api/sessions/search?q=${encodeURIComponent(query)}&limit=10`);
    const data = await res.json();
    const results: Array<{ thread_id: string; role: string; snippet: string; ts: string }> = data.results || [];
    if (!results.length) return `_No messages found for_ \`${query}\`.`;
    const lines = results.map(r => {
      const ts = r.ts?.slice(0, 19) || "";
      return `- **[${r.role}]** ${ts} — ${r.snippet} _(thread \`${r.thread_id.slice(0, 8)}\`)_`;
    });
    return `## Search: "${query}" (${results.length} results)\n\n` + lines.join("\n");
  } catch (e) {
    return `Search failed: ${e}`;
  }
}

async function runClear(_args: string): Promise<string> {
  if (typeof window !== "undefined" && confirm("Clear current conversation?")) {
    // Signal the page to clear
    window.dispatchEvent(new CustomEvent("slash:clear"));
    return "_Conversation cleared._";
  }
  return "_Cancelled._";
}

async function runHelp(_args: string): Promise<string> {
  const cmds = Object.values(SLASH_COMMANDS);
  const uniqueByName = new Map<string, SlashCommand>();
  for (const c of cmds) if (!uniqueByName.has(c.name)) uniqueByName.set(c.name, c);
  const lines = Array.from(uniqueByName.values()).map(c => {
    const aliases = c.aliases?.length ? ` _(alias: ${c.aliases.map(a => `\`/${a}\``).join(", ")})_` : "";
    return `- \`/${c.name}\` — ${c.description}${aliases}`;
  });
  lines.push("- `/mcp__<server>__<prompt> [json-args]` — Run an MCP prompt by server/prompt name");
  return "## Slash Commands\n\n" + lines.join("\n");
}

async function runMcpPromptCommand(commandName: string, args: string): Promise<{ ok: boolean; command: string; output: string }> {
  const parts = commandName.split("__");
  if (parts.length < 3) {
    return {
      ok: false,
      command: commandName,
      output: "Usage: `/mcp__<server>__<prompt> [json-args]`",
    };
  }
  const serverName = parts[1];
  const promptName = parts.slice(2).join("__");
  let parsedArgs: Record<string, unknown> = {};
  if (args.trim()) {
    try {
      parsedArgs = JSON.parse(args);
    } catch (e) {
      return {
        ok: false,
        command: commandName,
        output: `Invalid JSON args for /${commandName}: ${e}`,
      };
    }
  }
  try {
    const result = await callMcpPrompt(serverName, promptName, parsedArgs);
    const output = typeof result === "string" ? result : JSON.stringify(result, null, 2);
    return { ok: true, command: commandName, output };
  } catch (e) {
    return {
      ok: false,
      command: commandName,
      output: `Error running /${commandName}: ${e}`,
    };
  }
}

export const SLASH_COMMANDS: Record<string, SlashCommand> = {
  compact: {
    name: "compact",
    description: "Show context compaction state",
    handler: runCompact,
  },
  tasks: {
    name: "tasks",
    description: "List recent tasks",
    handler: runTasks,
  },
  statusline: {
    name: "statusline",
    description: "Show session/runtime status summary",
    handler: runStatusline,
    aliases: ["status"],
  },
  status: {
    name: "statusline",
    description: "Show session/runtime status summary",
    handler: runStatusline,
    aliases: ["status"],
  },
  usage: {
    name: "usage",
    description: "Show session token usage and cost",
    handler: runUsage,
    aliases: ["cost"],
  },
  cost: {
    name: "usage",
    description: "Show session token usage and cost",
    handler: runUsage,
    aliases: ["cost"],
  },
  models: {
    name: "models",
    description: "List available LLM models",
    handler: runModels,
    aliases: ["model"],
  },
  model: {
    name: "models",
    description: "List available LLM models",
    handler: runModels,
    aliases: ["model"],
  },
  search: {
    name: "search",
    description: "Search past conversations (FTS5 full-text)",
    handler: runSearch,
    aliases: ["find"],
  },
  find: {
    name: "search",
    description: "Search past conversations (FTS5 full-text)",
    handler: runSearch,
    aliases: ["find"],
  },
  clear: {
    name: "clear",
    description: "Clear current conversation",
    handler: runClear,
  },
  help: {
    name: "help",
    description: "List all slash commands",
    handler: runHelp,
    aliases: ["?"],
  },
  "?": {
    name: "help",
    description: "List all slash commands",
    handler: runHelp,
    aliases: ["?"],
  },
};

export function isSlashCommand(input: string): boolean {
  return input.trim().startsWith("/") && !input.trim().startsWith("//");
}

export async function dispatchSlashCommand(input: string): Promise<{
  ok: boolean;
  command: string;
  output: string;
}> {
  const trimmed = input.trim().slice(1);  // strip leading "/"
  const [cmd, ...rest] = trimmed.split(/\s+/);
  const args = rest.join(" ");
  if (cmd?.toLowerCase().startsWith("mcp__")) {
    return runMcpPromptCommand(cmd, args);
  }
  const command = SLASH_COMMANDS[cmd?.toLowerCase()];
  if (!command) {
    return {
      ok: false,
      command: cmd || "",
      output: `Unknown command \`/${cmd}\`. Try \`/help\` for the list.`,
    };
  }
  try {
    const output = await command.handler(args);
    return { ok: true, command: command.name, output };
  } catch (e) {
    return {
      ok: false,
      command: command.name,
      output: `Error running /${command.name}: ${e}`,
    };
  }
}

export function getSlashCompletions(input: string): SlashCommand[] {
  if (!input.startsWith("/")) return [];
  const prefix = input.slice(1).toLowerCase();
  const unique = new Map<string, SlashCommand>();
  for (const [key, cmd] of Object.entries(SLASH_COMMANDS)) {
    if (!key.startsWith(prefix)) continue;
    if (!unique.has(cmd.name)) unique.set(cmd.name, cmd);
  }
  return Array.from(unique.values());
}
