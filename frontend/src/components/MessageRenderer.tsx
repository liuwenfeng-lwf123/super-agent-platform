"use client";

import React, { useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import {
  ChevronDown,
  ChevronRight,
  Copy,
  Check,
  Download,
  Maximize2,
  Search,
  Code2,
  Terminal,
  Wrench,
  Globe,
  FileText,
  Brain,
} from "lucide-react";

function InlineCode({ children }: { children?: React.ReactNode }) {
  return (
    <code
      className="px-1.5 py-0.5 rounded text-xs font-mono"
      style={{
        background: "var(--bg-secondary)",
        border: "1px solid var(--border-color)",
      }}
    >
      {children}
    </code>
  );
}

function CodeBlock({
  className,
  children,
}: {
  className?: string;
  children?: React.ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  const match = /language-(\w+)/.exec(className || "");
  const lang = match ? match[1] : "text";
  const codeStr = String(children).replace(/\n$/, "");

  const handleCopy = () => {
    navigator.clipboard.writeText(codeStr);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="relative group my-3 rounded-xl overflow-hidden border" style={{ borderColor: "var(--border-color)" }}>
      <div
        className="flex items-center justify-between px-4 py-2 text-xs"
        style={{ background: "var(--bg-secondary)", color: "var(--text-secondary)" }}
      >
        <span className="font-medium">{lang}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 px-2 py-0.5 rounded hover:opacity-80 transition-opacity"
          style={{ color: "var(--text-secondary)" }}
        >
          {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="!m-0 !rounded-none !border-0 overflow-x-auto !p-4" style={{ background: "var(--bg-primary)" }}>
        <code className={className}>{children}</code>
      </pre>
    </div>
  );
}

function ThinkingBlock({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      className="my-2 rounded-xl border overflow-hidden"
      style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs font-medium transition-colors hover:opacity-80"
        style={{ color: "var(--accent)" }}
      >
        <Brain className="w-3.5 h-3.5" />
        <span>Thinking Process</span>
        {expanded ? (
          <ChevronDown className="w-3.5 h-3.5 ml-auto" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5 ml-auto" />
        )}
      </button>
      {expanded && (
        <div
          className="px-4 pb-3 text-xs leading-relaxed whitespace-pre-wrap font-mono"
          style={{ color: "var(--text-secondary)" }}
        >
          {content}
        </div>
      )}
    </div>
  );
}

function getToolIcon(tool: string) {
  const t = tool.toLowerCase();
  if (t.includes("search") || t.includes("web_search")) return Search;
  if (t.includes("python") || t.includes("javascript") || t.includes("execute")) return Terminal;
  if (t.includes("code") || t.includes("sandbox")) return Code2;
  if (t.includes("preview") || t.includes("html") || t.includes("web")) return Globe;
  if (t.includes("file") || t.includes("read") || t.includes("write")) return FileText;
  return Wrench;
}

function getToolLabel(tool: string) {
  const t = tool.toLowerCase();
  if (t.includes("web_search")) return "Web Search";
  if (t.includes("search")) return "Search";
  if (t.includes("execute_python")) return "Run Python";
  if (t.includes("execute_javascript")) return "Run JavaScript";
  if (t.includes("python")) return "Python";
  if (t.includes("javascript")) return "JavaScript";
  if (t.includes("calculate")) return "Calculate";
  if (t.includes("time")) return "Current Time";
  return tool;
}

export interface ToolCallInfo {
  tool: string;
  status: string;
  input?: string;
  output?: string;
}

function ToolCallCard({ tc }: { tc: ToolCallInfo }) {
  const [showOutput, setShowOutput] = useState(false);
  const Icon = getToolIcon(tc.tool);
  const label = getToolLabel(tc.tool);
  const isRunning = tc.status === "running";

  return (
    <div
      className="rounded-lg border p-3 transition-all"
      style={{
        background: "var(--bg-primary)",
        borderColor: isRunning ? "var(--accent)" : "var(--border-color)",
        boxShadow: isRunning ? "0 0 0 1px var(--accent)" : "none",
      }}
    >
      <div className="flex items-center gap-2">
        <span
          className={`w-2 h-2 rounded-full flex-shrink-0 ${
            tc.status === "completed"
              ? "bg-green-400"
              : tc.status === "failed"
              ? "bg-red-400"
              : "bg-yellow-400 animate-pulse"
          }`}
        />
        <Icon className="w-3.5 h-3.5 flex-shrink-0" style={{ color: "var(--accent)" }} />
        <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
          {label}
        </span>
        <span className="text-xs ml-auto" style={{ color: "var(--text-secondary)" }}>
          {isRunning ? "Running..." : tc.status === "completed" ? "Done" : tc.status}
        </span>
      </div>
      {tc.input && (
        <div
          className="mt-2 text-xs font-mono p-2 rounded-lg truncate"
          style={{ background: "var(--bg-secondary)", color: "var(--text-secondary)" }}
          title={tc.input}
        >
          {tc.input.length > 150 ? tc.input.slice(0, 150) + "..." : tc.input}
        </div>
      )}
      {tc.output && (
        <div className="mt-2">
          <button
            onClick={() => setShowOutput(!showOutput)}
            className="text-xs flex items-center gap-1 hover:opacity-80"
            style={{ color: "var(--accent)" }}
          >
            {showOutput ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            {showOutput ? "Hide result" : "Show result"}
          </button>
          {showOutput && (
            <div
              className="mt-1.5 p-2 rounded-lg text-xs font-mono whitespace-pre-wrap max-h-48 overflow-y-auto"
              style={{ background: "var(--bg-secondary)", color: "var(--text-secondary)" }}
            >
              {tc.output.length > 1000 ? tc.output.slice(0, 1000) + "\n... (truncated)" : tc.output}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ToolCallsPanel({ toolCalls }: { toolCalls: ToolCallInfo[] }) {
  if (toolCalls.length === 0) return null;

  return (
    <div
      className="max-w-3xl mx-auto mb-4 p-3 rounded-xl border"
      style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)" }}
    >
      <div className="flex items-center gap-1.5 text-xs font-medium mb-2" style={{ color: "var(--accent)" }}>
        <Wrench className="w-3.5 h-3.5" />
        <span>Tool Calls ({toolCalls.length})</span>
      </div>
      <div className="space-y-2">
        {toolCalls.map((tc, i) => (
          <ToolCallCard key={`${tc.tool}-${i}`} tc={tc} />
        ))}
      </div>
    </div>
  );
}

interface MessageContentProps {
  content: string;
  isUser?: boolean;
}

export function MessageContent({ content, isUser }: MessageContentProps) {
  if (isUser) {
    return <div className="whitespace-pre-wrap text-sm">{content}</div>;
  }

  const thinkingMatch = content.match(/<think[^>]*>([\s\S]*?)<\/think>/g);
  let displayContent = content;
  let thinkingParts: string[] = [];

  if (thinkingMatch) {
    thinkingParts = thinkingMatch.map((m) =>
      m.replace(/<think[^>]*>/, "").replace(/<\/think>/, "")
    );
    displayContent = content.replace(/<think[^>]*>[\s\S]*?<\/think>/g, "").trim();
  }

  return (
    <div className="text-sm">
      {thinkingParts.map((t, i) => (
        <ThinkingBlock key={i} content={t.trim()} />
      ))}
      <div className="markdown-body">
        <ReactMarkdown
          rehypePlugins={[rehypeHighlight]}
          components={{
            pre({ children }) {
              return <>{children}</>;
            },
            code({ className, children }) {
              if (!className) {
                return <InlineCode>{children}</InlineCode>;
              }
              return <CodeBlock className={className}>{children}</CodeBlock>;
            },
            a({ href, children }) {
              return (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: "var(--accent)", textDecoration: "underline" }}
                >
                  {children}
                </a>
              );
            },
            table({ children }) {
              return (
                <div className="overflow-x-auto my-2">
                  <table
                    className="min-w-full text-xs border-collapse"
                    style={{ border: "1px solid var(--border-color)" }}
                  >
                    {children}
                  </table>
                </div>
              );
            },
            th({ children }) {
              return (
                <th
                  className="px-3 py-2 text-left font-semibold"
                  style={{ background: "var(--bg-secondary)", borderBottom: "1px solid var(--border-color)", color: "var(--text-primary)" }}
                >
                  {children}
                </th>
              );
            },
            td({ children }) {
              return (
                <td
                  className="px-3 py-2"
                  style={{ borderBottom: "1px solid var(--border-color)", color: "var(--text-primary)" }}
                >
                  {children}
                </td>
              );
            },
          }}
        >
          {displayContent}
        </ReactMarkdown>
      </div>
    </div>
  );
}

interface WebPreviewPanelProps {
  html: string;
  onClose: () => void;
}

export function WebPreviewPanel({ html, onClose }: WebPreviewPanelProps) {
  const [fullscreen, setFullscreen] = useState(false);
  const [showCode, setShowCode] = useState(false);

  const handleDownload = () => {
    const blob = new Blob([html], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "preview.html";
    a.click();
    URL.revokeObjectURL(url);
  };

  if (fullscreen) {
    return (
      <div className="fixed inset-0 z-50 flex flex-col" style={{ background: "var(--bg-primary)" }}>
        <div className="flex items-center justify-between px-4 py-2 border-b" style={{ borderColor: "var(--border-color)" }}>
          <div className="flex items-center gap-2 text-xs font-medium" style={{ color: "var(--accent)" }}>
            <Globe className="w-3.5 h-3.5" /> Fullscreen Preview
          </div>
          <div className="flex items-center gap-2">
            <button onClick={handleDownload} className="p-1.5 rounded-lg hover:opacity-80" style={{ color: "var(--text-secondary)" }} title="Download">
              <Download className="w-4 h-4" />
            </button>
            <button onClick={() => setFullscreen(false)} className="p-1.5 rounded-lg hover:opacity-80" style={{ color: "var(--text-secondary)" }} title="Exit fullscreen">
              <Maximize2 className="w-4 h-4" />
            </button>
          </div>
        </div>
        <iframe srcDoc={html} className="flex-1 w-full border-0" sandbox="allow-scripts" />
      </div>
    );
  }

  return (
    <div className="border-t" style={{ borderColor: "var(--border-color)" }}>
      <div className="flex items-center justify-between px-4 py-1.5 border-b" style={{ borderColor: "var(--border-color)" }}>
        <div className="flex items-center gap-2 text-xs font-medium" style={{ color: "var(--accent)" }}>
          <Globe className="w-3.5 h-3.5" /> Preview
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setShowCode(!showCode)}
            className="p-1 rounded hover:opacity-70 text-xs flex items-center gap-1 px-2 py-0.5"
            style={{ color: "var(--text-secondary)", background: showCode ? "var(--bg-secondary)" : "transparent" }}
          >
            <Code2 className="w-3 h-3" /> Code
          </button>
          <button onClick={handleDownload} className="p-1 rounded hover:opacity-70" style={{ color: "var(--text-secondary)" }} title="Download HTML">
            <Download className="w-3.5 h-3.5" />
          </button>
          <button onClick={() => setFullscreen(true)} className="p-1 rounded hover:opacity-70" style={{ color: "var(--text-secondary)" }} title="Fullscreen">
            <Maximize2 className="w-3.5 h-3.5" />
          </button>
          <button onClick={onClose} className="p-1 rounded hover:opacity-70" style={{ color: "var(--text-secondary)" }}>
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
      {showCode ? (
        <div
          className="p-3 text-xs font-mono whitespace-pre-wrap overflow-auto max-h-48"
          style={{ background: "var(--bg-primary)", color: "var(--text-secondary)" }}
        >
          {html}
        </div>
      ) : (
        <iframe srcDoc={html} className="w-full border-0" style={{ height: "200px" }} sandbox="allow-scripts" />
      )}
    </div>
  );
}

function X({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M18 6 6 18" /><path d="m6 6 12 12" />
    </svg>
  );
}
