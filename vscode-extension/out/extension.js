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
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const api_1 = require("./api");
let api;
let outputChannel;
let currentThreadId;
function getConfig() {
    const cfg = vscode.workspace.getConfiguration("tiangongflow");
    return {
        serverUrl: cfg.get("serverUrl", "http://localhost:8001"),
        token: cfg.get("apiToken", ""),
        model: cfg.get("model", ""),
        mode: cfg.get("mode", "standard"),
    };
}
function activate(context) {
    const cfg = getConfig();
    api = new api_1.TianGongFlowAPI(cfg.serverUrl, cfg.token);
    outputChannel = vscode.window.createOutputChannel("TianGongFlow");
    // Re-read config on change
    vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration("tiangongflow")) {
            const c = getConfig();
            api.updateConfig(c.serverUrl, c.token);
        }
    });
    // Command: Send message (input box)
    context.subscriptions.push(vscode.commands.registerCommand("tiangongflow.sendMessage", async () => {
        const message = await vscode.window.showInputBox({
            prompt: "Enter your message for TianGongFlow",
            placeHolder: "e.g. Fix the bug in auth.py",
        });
        if (!message) {
            return;
        }
        await streamToOutput(message);
    }));
    // Command: Explain selection
    context.subscriptions.push(vscode.commands.registerCommand("tiangongflow.explainSelection", async () => {
        const code = getSelectedCode();
        if (!code) {
            return;
        }
        await streamToOutput(`请解释以下代码:\n\`\`\`\n${code}\n\`\`\``);
    }));
    // Command: Fix selection
    context.subscriptions.push(vscode.commands.registerCommand("tiangongflow.fixSelection", async () => {
        const code = getSelectedCode();
        if (!code) {
            return;
        }
        await streamToOutput(`请修复以下代码中的问题:\n\`\`\`\n${code}\n\`\`\``);
    }));
    // Command: Refactor selection
    context.subscriptions.push(vscode.commands.registerCommand("tiangongflow.refactorSelection", async () => {
        const code = getSelectedCode();
        if (!code) {
            return;
        }
        await streamToOutput(`请重构以下代码，使其更清晰高效:\n\`\`\`\n${code}\n\`\`\``);
    }));
    // Command: Open panel (webview)
    const chatProvider = new ChatViewProvider(context.extensionUri);
    context.subscriptions.push(vscode.window.registerWebviewViewProvider("tiangongflow.chatPanel", chatProvider));
    context.subscriptions.push(vscode.commands.registerCommand("tiangongflow.openPanel", () => {
        vscode.commands.executeCommand("tiangongflow.chatPanel.focus");
    }));
    // Status bar
    const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBar.text = "$(hubot) TianGongFlow";
    statusBar.command = "tiangongflow.sendMessage";
    statusBar.tooltip = "Send a message to TianGongFlow AI agent";
    statusBar.show();
    context.subscriptions.push(statusBar);
    outputChannel.appendLine("TianGongFlow extension activated");
}
function deactivate() {
    outputChannel?.dispose();
}
function getSelectedCode() {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        vscode.window.showWarningMessage("No active editor");
        return undefined;
    }
    const selection = editor.selection;
    if (selection.isEmpty) {
        vscode.window.showWarningMessage("No code selected");
        return undefined;
    }
    return editor.document.getText(selection);
}
async function streamToOutput(message) {
    const cfg = getConfig();
    outputChannel.show(true);
    outputChannel.appendLine(`\n━━━ You: ${message.slice(0, 80)}${message.length > 80 ? "..." : ""} ━━━`);
    await vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title: "TianGongFlow",
        cancellable: true,
    }, async (progress, token) => {
        const controller = new AbortController();
        token.onCancellationRequested(() => controller.abort());
        progress.report({ message: "Thinking..." });
        let collected = "";
        try {
            await api.sendMessage({
                message,
                threadId: currentThreadId,
                model: cfg.model || undefined,
                mode: cfg.mode,
            }, (event) => {
                if (event.type === "token" && event.content) {
                    collected += event.content;
                    outputChannel.append(event.content);
                }
                else if (event.type === "tool_call" && event.data) {
                    const tool = event.data.tool || "?";
                    outputChannel.appendLine(`\n  🔧 Using: ${tool}`);
                    progress.report({ message: `Using ${tool}...` });
                }
                else if (event.type === "tool_result" && event.data) {
                    const status = event.data.status;
                    outputChannel.appendLine(`  ${status === "error" ? "✗" : "✓"} Done`);
                }
                else if (event.type === "file_diff" && event.data) {
                    const path = event.data.path || "?";
                    const adds = event.data.additions || 0;
                    const dels = event.data.deletions || 0;
                    outputChannel.appendLine(`  📝 ${path} (+${adds} -${dels})`);
                }
                else if (event.type === "done") {
                    if (event.thread_id) {
                        currentThreadId = event.thread_id;
                    }
                    progress.report({ message: "Done", increment: 100 });
                }
                else if (event.type === "error") {
                    outputChannel.appendLine(`\n  ❌ Error: ${event.content || "Unknown"}`);
                }
            }, controller.signal);
        }
        catch (err) {
            if (err instanceof Error && err.message === "Aborted") {
                outputChannel.appendLine("\n  [Cancelled by user]");
            }
            else {
                const msg = err instanceof Error ? err.message : String(err);
                outputChannel.appendLine(`\n  ❌ ${msg}`);
                vscode.window.showErrorMessage(`TianGongFlow: ${msg}`);
            }
        }
        if (!collected) {
            outputChannel.appendLine("(No response)");
        }
        outputChannel.appendLine("");
    });
}
/** Webview chat panel in the sidebar */
class ChatViewProvider {
    constructor(extensionUri) {
        this.extensionUri = extensionUri;
    }
    resolveWebviewView(webviewView) {
        webviewView.webview.options = {
            enableScripts: true,
        };
        webviewView.webview.html = this.getHtml();
        webviewView.webview.onDidReceiveMessage(async (message) => {
            if (message.type === "send") {
                const cfg = getConfig();
                const tokens = [];
                try {
                    await api.sendMessage({
                        message: message.text,
                        threadId: currentThreadId,
                        model: cfg.model || undefined,
                        mode: cfg.mode,
                    }, (event) => {
                        if (event.type === "token" && event.content) {
                            tokens.push(event.content);
                            webviewView.webview.postMessage({ type: "token", content: event.content });
                        }
                        else if (event.type === "done") {
                            if (event.thread_id) {
                                currentThreadId = event.thread_id;
                            }
                            webviewView.webview.postMessage({ type: "done" });
                        }
                        else if (event.type === "error") {
                            webviewView.webview.postMessage({ type: "error", content: event.content });
                        }
                        else if (event.type === "file_diff" && event.data) {
                            webviewView.webview.postMessage({ type: "file_diff", data: event.data });
                        }
                    });
                }
                catch (err) {
                    const msg = err instanceof Error ? err.message : String(err);
                    webviewView.webview.postMessage({ type: "error", content: msg });
                }
            }
        });
    }
    getHtml() {
        return `<!DOCTYPE html>
<html>
<head>
<style>
  body { font-family: var(--vscode-font-family); padding: 8px; margin: 0; color: var(--vscode-foreground); }
  #messages { max-height: 70vh; overflow-y: auto; margin-bottom: 8px; }
  .msg { margin: 4px 0; padding: 6px 8px; border-radius: 6px; font-size: 13px; line-height: 1.4; white-space: pre-wrap; word-break: break-word; }
  .user { background: var(--vscode-input-background); }
  .assistant { background: var(--vscode-editor-background); border: 1px solid var(--vscode-panel-border); }
  .diff { font-family: monospace; font-size: 11px; margin: 4px 0; padding: 4px 6px; border-radius: 4px; background: var(--vscode-editor-background); }
  .diff-add { color: var(--vscode-gitDecoration-addedResourceForeground, #4ade80); }
  .diff-del { color: var(--vscode-gitDecoration-deletedResourceForeground, #f87171); }
  #input-row { display: flex; gap: 4px; }
  #input { flex: 1; padding: 6px 8px; border: 1px solid var(--vscode-input-border); background: var(--vscode-input-background); color: var(--vscode-input-foreground); border-radius: 4px; font-size: 13px; }
  #send { padding: 6px 12px; background: var(--vscode-button-background); color: var(--vscode-button-foreground); border: none; border-radius: 4px; cursor: pointer; font-size: 13px; }
  #send:hover { background: var(--vscode-button-hoverBackground); }
</style>
</head>
<body>
  <div id="messages"></div>
  <div id="input-row">
    <input id="input" placeholder="Ask TianGongFlow..." />
    <button id="send">Send</button>
  </div>
  <script>
    const vscode = acquireVsCodeApi();
    const messagesEl = document.getElementById('messages');
    const inputEl = document.getElementById('input');
    let currentAssistant = null;

    document.getElementById('send').addEventListener('click', send);
    inputEl.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });

    function send() {
      const text = inputEl.value.trim();
      if (!text) return;
      addMessage('user', text);
      currentAssistant = addMessage('assistant', '');
      inputEl.value = '';
      vscode.postMessage({ type: 'send', text });
    }

    function addMessage(role, content) {
      const div = document.createElement('div');
      div.className = 'msg ' + role;
      div.textContent = content;
      messagesEl.appendChild(div);
      messagesEl.scrollTop = messagesEl.scrollHeight;
      return div;
    }

    window.addEventListener('message', (e) => {
      const msg = e.data;
      if (msg.type === 'token' && currentAssistant) {
        currentAssistant.textContent += msg.content;
        messagesEl.scrollTop = messagesEl.scrollHeight;
      } else if (msg.type === 'done') {
        currentAssistant = null;
      } else if (msg.type === 'error') {
        if (currentAssistant) {
          currentAssistant.textContent += '\\n❌ ' + (msg.content || 'Error');
        }
        currentAssistant = null;
      } else if (msg.type === 'file_diff' && msg.data) {
        const d = msg.data;
        const div = document.createElement('div');
        div.className = 'diff';
        const icon = d.status === 'added' ? '🆕' : d.status === 'deleted' ? '🗑️' : '📝';
        div.innerHTML = '<strong>' + icon + ' ' + (d.path || '?') + '</strong>' +
          (d.additions ? ' <span class="diff-add">+' + d.additions + '</span>' : '') +
          (d.deletions ? ' <span class="diff-del">-' + d.deletions + '</span>' : '');
        messagesEl.appendChild(div);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
    });
  </script>
</body>
</html>`;
    }
}
//# sourceMappingURL=extension.js.map