"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.TianGongFlowAPI = void 0;
/**
 * TianGongFlow backend API client for VS Code extension.
 * Handles SSE streaming and REST calls.
 */
const https = __importStar(require("https"));
const http = __importStar(require("http"));
class TianGongFlowAPI {
    constructor(serverUrl, token = "") {
        this.serverUrl = serverUrl;
        this.token = token;
    }
    get headers() {
        const h = {
            "Content-Type": "application/json",
        };
        if (this.token) {
            h["Authorization"] = `Bearer ${this.token}`;
        }
        return h;
    }
    updateConfig(serverUrl, token) {
        this.serverUrl = serverUrl;
        this.token = token;
    }
    /** Health check */
    async health() {
        try {
            const res = await this.fetch("GET", "/health");
            return res?.status === "healthy";
        }
        catch {
            return false;
        }
    }
    /** List available models */
    async listModels() {
        try {
            const res = await this.fetch("GET", "/models");
            return res?.models?.map((m) => m.id) || [];
        }
        catch {
            return [];
        }
    }
    /** Send message with SSE streaming */
    async sendMessage(options, onEvent, signal) {
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
            const req = mod.request({
                hostname: url.hostname,
                port: url.port || (isHttps ? 443 : 80),
                path: url.pathname,
                method: "POST",
                headers: this.headers,
            }, (res) => {
                let buffer = "";
                res.on("data", (chunk) => {
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
                                const event = JSON.parse(jsonStr);
                                onEvent(event);
                            }
                            catch {
                                // ignore parse errors
                            }
                        }
                    }
                });
                res.on("end", () => resolve());
                res.on("error", reject);
            });
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
    async fetch(method, path, body) {
        return new Promise((resolve, reject) => {
            const url = new URL(`${this.serverUrl}${path}`);
            const isHttps = url.protocol === "https:";
            const mod = isHttps ? https : http;
            const req = mod.request({
                hostname: url.hostname,
                port: url.port || (isHttps ? 443 : 80),
                path: url.pathname + url.search,
                method,
                headers: this.headers,
            }, (res) => {
                let data = "";
                res.on("data", (chunk) => (data += chunk.toString()));
                res.on("end", () => {
                    try {
                        resolve(JSON.parse(data));
                    }
                    catch {
                        resolve(data);
                    }
                });
                res.on("error", reject);
            });
            req.on("error", reject);
            if (body) {
                req.write(JSON.stringify(body));
            }
            req.end();
        });
    }
}
exports.TianGongFlowAPI = TianGongFlowAPI;
//# sourceMappingURL=api.js.map