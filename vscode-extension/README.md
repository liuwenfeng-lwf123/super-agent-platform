# TianGongFlow VS Code Extension

AI Super Agent integration for Visual Studio Code.

## Features

- **Chat Panel** — sidebar webview for conversing with the agent
- **Right-click Context Menu** — explain, fix, or refactor selected code
- **Streaming Output** — real-time token streaming in the Output panel
- **File Diff Display** — see file changes as they happen
- **Configurable** — server URL, API token, model, and mode

## Setup

```bash
cd vscode-extension
npm install
npm run compile
```

Then press F5 in VS Code to launch the Extension Development Host.

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `tiangongflow.serverUrl` | `http://localhost:8001` | Backend server URL |
| `tiangongflow.apiToken` | (empty) | API auth token |
| `tiangongflow.model` | (empty) | Model override |
| `tiangongflow.mode` | `standard` | Agent mode: flash/standard/pro/ultra |

## Commands

- `TianGongFlow: Send Message` — open input box to chat
- `TianGongFlow: Explain Selection` — explain selected code
- `TianGongFlow: Fix Selection` — fix bugs in selected code
- `TianGongFlow: Refactor Selection` — refactor selected code
- `TianGongFlow: Open Chat Panel` — focus the sidebar chat
