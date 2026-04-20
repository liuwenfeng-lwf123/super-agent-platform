"use client";

import React, { useState, useEffect } from "react";
import {
  FolderOpen,
  File,
  FileCode,
  FileText,
  Image,
  Download,
  RefreshCw,
  ChevronRight,
  ChevronDown,
  Eye,
  X,
  Maximize2,
} from "lucide-react";
import { fetchWorkspaceFiles, getWorkspaceDownloadUrl } from "@/lib/api";
import type { WorkspaceFile } from "@/types";

interface WorkspacePanelProps {
  threadId: string | null;
  onPreviewHtml?: (html: string) => void;
}

function getFileIcon(name: string, isDir: boolean) {
  if (isDir) return FolderOpen;
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["html", "css", "js", "ts", "jsx", "tsx", "py", "json"].includes(ext)) return FileCode;
  if (["md", "txt", "pdf", "doc"].includes(ext)) return FileText;
  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) return Image;
  return File;
}

function isHtmlFile(name: string) {
  return name.endsWith(".html") || name.endsWith(".htm");
}

function isPreviewable(name: string) {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  return ["html", "htm", "md", "txt", "json", "js", "ts", "py", "css", "svg", "xml", "yaml", "yml"].includes(ext);
}

export function WorkspacePanel({ threadId, onPreviewHtml }: WorkspacePanelProps) {
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [currentPath, setCurrentPath] = useState(".");
  const [previewFile, setPreviewFile] = useState<{ name: string; content: string; path: string } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  useEffect(() => {
    if (threadId) loadFiles();
  }, [threadId, currentPath]);

  const loadFiles = async () => {
    if (!threadId) return;
    setLoading(true);
    try {
      const data = await fetchWorkspaceFiles(threadId, currentPath);
      if (data.entries) {
        setFiles(data.entries);
      } else {
        setFiles([]);
      }
    } catch {
      setFiles([]);
    }
    setLoading(false);
  };

  const handleNavigate = (file: WorkspaceFile) => {
    if (file.is_dir) {
      setCurrentPath(currentPath === "." ? file.name : `${currentPath}/${file.name}`);
      return;
    }
    if (isPreviewable(file.name)) {
      handlePreview(file);
    }
  };

  const handlePreview = async (file: WorkspaceFile) => {
    if (!threadId) return;
    setPreviewLoading(true);
    const filePath = currentPath === "." ? file.name : `${currentPath}/${file.name}`;
    try {
      const res = await fetch(`/api/workspace/${threadId}/read?path=${encodeURIComponent(filePath)}`);
      const data = await res.json();
      if (data.content) {
        setPreviewFile({ name: file.name, content: data.content, path: filePath });
        if (isHtmlFile(file.name) && onPreviewHtml) {
          onPreviewHtml(data.content);
        }
      }
    } catch {}
    setPreviewLoading(false);
  };

  const pathParts = currentPath === "." ? [] : currentPath.split("/");

  if (!threadId) {
    return (
      <div
        className="w-60 border-l flex flex-col"
        style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}
      >
        <div className="p-3 border-b" style={{ borderColor: "var(--border-color)" }}>
          <div className="flex items-center gap-2 text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
            <FolderOpen className="w-4 h-4" /> Workspace
          </div>
        </div>
        <div className="flex-1 flex items-center justify-center p-4">
          <p className="text-xs text-center" style={{ color: "var(--text-secondary)" }}>
            Start a conversation to see workspace files
          </p>
        </div>
      </div>
    );
  }

  return (
    <div
      className="w-60 border-l flex flex-col"
      style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}
    >
      <div className="p-3 border-b" style={{ borderColor: "var(--border-color)" }}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs font-medium" style={{ color: "var(--accent)" }}>
            <FolderOpen className="w-4 h-4" /> Workspace
          </div>
          <button onClick={loadFiles} className="p-1 rounded hover:opacity-70" style={{ color: "var(--text-secondary)" }}>
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
        <div className="flex items-center gap-1 mt-1 text-xs flex-wrap" style={{ color: "var(--text-secondary)" }}>
          <button onClick={() => setCurrentPath(".")} className="hover:opacity-70">root</button>
          {pathParts.map((part, i) => (
            <React.Fragment key={i}>
              <ChevronRight className="w-3 h-3" />
              <button
                onClick={() => setCurrentPath(pathParts.slice(0, i + 1).join("/"))}
                className="hover:opacity-70"
              >
                {part}
              </button>
            </React.Fragment>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2 scrollbar-thin">
        {currentPath !== "." && (
          <button
            onClick={() => {
              const parts = currentPath.split("/");
              parts.pop();
              setCurrentPath(parts.length === 0 ? "." : parts.join("/"));
            }}
            className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs transition-colors hover:opacity-80"
            style={{ color: "var(--text-secondary)" }}
          >
            <ChevronDown className="w-3.5 h-3.5 rotate-90" /> ..
          </button>
        )}
        {files.map((file) => {
          const Icon = getFileIcon(file.name, file.is_dir);
          const canPreview = !file.is_dir && isPreviewable(file.name);
          const isHtml = !file.is_dir && isHtmlFile(file.name);
          return (
            <div
              key={file.name}
              className="group flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-xs transition-colors cursor-pointer hover:opacity-80"
              style={{
                color: "var(--text-primary)",
                background: previewFile?.name === file.name ? "var(--accent-light)" : "transparent",
              }}
              onClick={() => handleNavigate(file)}
            >
              <Icon className="w-3.5 h-3.5 flex-shrink-0" style={{ color: file.is_dir ? "var(--accent)" : "var(--text-secondary)" }} />
              <span className="truncate flex-1">{file.name}</span>
              {!file.is_dir && (
                <span className="text-[10px] flex-shrink-0" style={{ color: "var(--text-secondary)" }}>
                  {file.size > 1024 ? `${(file.size / 1024).toFixed(0)}K` : `${file.size}B`}
                </span>
              )}
              {isHtml && (
                <button
                  onClick={(e) => { e.stopPropagation(); handlePreview(file); }}
                  className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:opacity-70"
                  style={{ color: "var(--accent)" }}
                  title="Preview"
                >
                  <Eye className="w-3 h-3" />
                </button>
              )}
              {!file.is_dir && (
                <a
                  href={getWorkspaceDownloadUrl(threadId, `${currentPath === "." ? "" : currentPath + "/"}${file.name}`)}
                  download={file.name}
                  onClick={(e) => e.stopPropagation()}
                  className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:opacity-70"
                  style={{ color: "var(--text-secondary)" }}
                >
                  <Download className="w-3 h-3" />
                </a>
              )}
            </div>
          );
        })}
        {files.length === 0 && !loading && (
          <p className="text-xs text-center py-4" style={{ color: "var(--text-secondary)" }}>
            Empty workspace
          </p>
        )}
      </div>

      {previewFile && (
        <div className="border-t" style={{ borderColor: "var(--border-color)" }}>
          <div className="flex items-center justify-between px-3 py-1.5" style={{ background: "var(--bg-secondary)" }}>
            <span className="text-xs font-medium truncate" style={{ color: "var(--accent)" }}>
              {previewFile.name}
            </span>
            <div className="flex items-center gap-1">
              {isHtmlFile(previewFile.name) && (
                <button
                  onClick={() => {
                    const win = window.open("", "_blank");
                    if (win) { win.document.write(previewFile.content); win.document.close(); }
                  }}
                  className="p-0.5 rounded hover:opacity-70"
                  style={{ color: "var(--text-secondary)" }}
                  title="Open in new tab"
                >
                  <Maximize2 className="w-3 h-3" />
                </button>
              )}
              <button
                onClick={() => setPreviewFile(null)}
                className="p-0.5 rounded hover:opacity-70"
                style={{ color: "var(--text-secondary)" }}
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          </div>
          {isHtmlFile(previewFile.name) ? (
            <iframe
              srcDoc={previewFile.content}
              className="w-full border-0"
              style={{ height: "200px" }}
              sandbox="allow-scripts"
            />
          ) : (
            <pre
              className="p-2 text-[10px] font-mono overflow-auto max-h-48 whitespace-pre-wrap"
              style={{ color: "var(--text-secondary)", background: "var(--bg-primary)" }}
            >
              {previewFile.content.length > 3000 ? previewFile.content.slice(0, 3000) + "\n... (truncated)" : previewFile.content}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
