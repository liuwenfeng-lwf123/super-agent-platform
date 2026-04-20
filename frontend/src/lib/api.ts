import type { ChatRequest, SSEEventData } from "@/types";

const API_BASE = "/api";

export async function sendMessage(
  request: ChatRequest,
  onEvent: (event: SSEEventData) => void,
  onError?: (error: Error) => void
): Promise<void> {
  const response = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(`HTTP error: ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    let currentEvent = "";
    for (const line of lines) {
      if (line.startsWith("event: ")) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        try {
          const data = JSON.parse(line.slice(6));
          onEvent({ type: currentEvent as SSEEventData["type"], ...data });
        } catch {
          // skip malformed data
        }
      }
    }
  }
}

export async function fetchThreads() {
  const res = await fetch(`${API_BASE}/threads`);
  return res.json();
}

export async function fetchThread(threadId: string) {
  const res = await fetch(`${API_BASE}/threads/${threadId}`);
  return res.json();
}

export async function deleteThread(threadId: string) {
  const res = await fetch(`${API_BASE}/threads/${threadId}`, { method: "DELETE" });
  return res.json();
}

export async function fetchModels() {
  const res = await fetch(`${API_BASE}/models`);
  return res.json();
}

export async function fetchSkills() {
  const res = await fetch(`${API_BASE}/skills`);
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
  const res = await fetch(`${API_BASE}/memory/${entryId}`, { method: "DELETE" });
  return res.json();
}

export async function fetchWorkspaceFiles(threadId: string, path: string = ".") {
  const res = await fetch(`${API_BASE}/workspace/${threadId}/files?path=${encodeURIComponent(path)}`);
  return res.json();
}

export function getWorkspaceDownloadUrl(threadId: string, filePath: string) {
  return `${API_BASE}/workspace/${threadId}/download/${filePath}`;
}

export function getOutputDownloadUrl(threadId: string, filePath: string) {
  return `${API_BASE}/outputs/${threadId}/download/${filePath}`;
}
