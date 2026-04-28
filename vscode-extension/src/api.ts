/**
 * TianGongFlow backend API client for VS Code extension.
 * Handles SSE streaming and REST calls.
 */
import * as https from "https";
import * as http from "http";

export interface SendOptions {
  message: string;
  threadId?: string;
  model?: string;
  mode?: string;
  skills?: string[];
}

export interface SSEEvent {
  type: string;
  content?: string;
  data?: Record<string, unknown>;
  thread_id?: string;
  usage?: Record<string, number>;
}

export class TianGongFlowAPI {
  constructor(
    private serverUrl: string,
    private token: string = ""
  ) {}

  private get headers(): Record<string, string> {
    const h: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (this.token) {
      h["Authorization"] = `Bearer ${this.token}`;
    }
    return h;
  }

  updateConfig(serverUrl: string, token: string) {
    this.serverUrl = serverUrl;
    this.token = token;
  }

  /** Health check */
  async health(): Promise<boolean> {
    try {
      const res = await this.fetch("GET", "/health");
      return res?.status === "healthy";
    } catch {
      return false;
    }
  }

  /** List available models */
  async listModels(): Promise<string[]> {
    try {
      const res = await this.fetch("GET", "/models");
      return res?.models?.map((m: { id: string }) => m.id) || [];
    } catch {
      return [];
    }
  }

  /** Send message with SSE streaming */
  async sendMessage(
    options: SendOptions,
    onEvent: (event: SSEEvent) => void,
    signal?: AbortSignal
  ): Promise<void> {
    const body = {
      message: options.message,
      thread_id: options.threadId,
      model: options.model || undefined,
      mode: options.mode || "standard",
      skills: options.skills,
    };

    return new Promise((resolve, reject) => {
      const url = new URL(`${this.serverUrl}/chat/send`);
      const isHttps = url.protocol === "https:";
      const mod = isHttps ? https : http;

      const req = mod.request(
        {
          hostname: url.hostname,
          port: url.port || (isHttps ? 443 : 80),
          path: url.pathname,
          method: "POST",
          headers: this.headers,
        },
        (res) => {
          let buffer = "";

          res.on("data", (chunk: Buffer) => {
            buffer += chunk.toString();
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
              if (line.startsWith("data: ")) {
                const jsonStr = line.slice(6).trim();
                if (!jsonStr || jsonStr === "[DONE]") {
                  continue;
                }
                try {
                  const event: SSEEvent = JSON.parse(jsonStr);
                  onEvent(event);
                } catch {
                  // ignore parse errors
                }
              }
            }
          });

          res.on("end", () => resolve());
          res.on("error", reject);
        }
      );

      if (signal) {
        signal.addEventListener("abort", () => {
          req.destroy();
          reject(new Error("Aborted"));
        });
      }

      req.on("error", reject);
      req.write(JSON.stringify(body));
      req.end();
    });
  }

  /** Generic JSON fetch */
  private async fetch(method: string, path: string, body?: unknown): Promise<any> {
    return new Promise((resolve, reject) => {
      const url = new URL(`${this.serverUrl}${path}`);
      const isHttps = url.protocol === "https:";
      const mod = isHttps ? https : http;

      const req = mod.request(
        {
          hostname: url.hostname,
          port: url.port || (isHttps ? 443 : 80),
          path: url.pathname + url.search,
          method,
          headers: this.headers,
        },
        (res) => {
          let data = "";
          res.on("data", (chunk: Buffer) => (data += chunk.toString()));
          res.on("end", () => {
            try {
              resolve(JSON.parse(data));
            } catch {
              resolve(data);
            }
          });
          res.on("error", reject);
        }
      );

      req.on("error", reject);
      if (body) {
        req.write(JSON.stringify(body));
      }
      req.end();
    });
  }
}
