"use client";

import React, { useState } from "react";

/**
 * DiffViewer — displays streaming file diffs during agent tool execution.
 * Renders unified diff with syntax highlighting for add/del/context lines.
 */

export interface FileDiff {
  path: string;
  status: "added" | "modified" | "deleted";
  binary: boolean;
  diff: string;
  hunks?: DiffHunk[];
  additions?: number;
  deletions?: number;
}

interface DiffHunk {
  id: string;
  header: string;
  lines: DiffLine[];
  truncated?: boolean;
}

interface DiffLine {
  type: "add" | "del" | "context";
  content: string;
}

interface DiffViewerProps {
  diffs: FileDiff[];
  collapsed?: boolean;
}

const statusIcon: Record<string, string> = {
  added: "🆕",
  modified: "📝",
  deleted: "🗑️",
};

const statusColor: Record<string, string> = {
  added: "text-green-500",
  modified: "text-yellow-500",
  deleted: "text-red-500",
};

export default function DiffViewer({ diffs, collapsed = true }: DiffViewerProps) {
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(
    collapsed ? new Set() : new Set(diffs.map((d) => d.path))
  );

  if (!diffs || diffs.length === 0) return null;

  const toggleFile = (path: string) => {
    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  const totalAdditions = diffs.reduce((sum, d) => sum + (d.additions || 0), 0);
  const totalDeletions = diffs.reduce((sum, d) => sum + (d.deletions || 0), 0);

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-900 overflow-hidden my-2 text-sm">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-zinc-800 border-b border-zinc-700">
        <span className="font-medium text-zinc-300">
          📂 {diffs.length} file{diffs.length !== 1 ? "s" : ""} changed
        </span>
        <span className="text-xs">
          <span className="text-green-400">+{totalAdditions}</span>{" "}
          <span className="text-red-400">-{totalDeletions}</span>
        </span>
      </div>

      {/* File list */}
      {diffs.map((diff) => (
        <div key={diff.path} className="border-b border-zinc-800 last:border-b-0">
          {/* File header */}
          <button
            onClick={() => toggleFile(diff.path)}
            className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-zinc-800 transition-colors text-left"
          >
            <span className="text-xs">{expandedFiles.has(diff.path) ? "▼" : "▶"}</span>
            <span>{statusIcon[diff.status] || "📄"}</span>
            <span className={`font-mono text-xs ${statusColor[diff.status] || "text-zinc-400"}`}>
              {diff.path}
            </span>
            {diff.additions || diff.deletions ? (
              <span className="ml-auto text-xs">
                {diff.additions ? <span className="text-green-400">+{diff.additions}</span> : null}
                {diff.additions && diff.deletions ? " " : null}
                {diff.deletions ? <span className="text-red-400">-{diff.deletions}</span> : null}
              </span>
            ) : null}
          </button>

          {/* Diff content */}
          {expandedFiles.has(diff.path) && (
            <div className="bg-zinc-950 overflow-x-auto">
              {diff.binary ? (
                <div className="px-4 py-2 text-zinc-500 italic text-xs">Binary file changed</div>
              ) : diff.hunks && diff.hunks.length > 0 ? (
                diff.hunks.map((hunk) => (
                  <div key={hunk.id} className="border-t border-zinc-800">
                    <div className="px-4 py-1 text-xs text-blue-400 bg-zinc-900 font-mono">
                      {hunk.header}
                    </div>
                    <div className="font-mono text-xs leading-5">
                      {hunk.lines.map((line, i) => (
                        <div
                          key={`${hunk.id}-${i}`}
                          className={`px-4 ${
                            line.type === "add"
                              ? "bg-green-900/20 text-green-300"
                              : line.type === "del"
                              ? "bg-red-900/20 text-red-300"
                              : "text-zinc-500"
                          }`}
                        >
                          <span className="select-none mr-2 text-zinc-600">
                            {line.type === "add" ? "+" : line.type === "del" ? "-" : " "}
                          </span>
                          {line.content}
                        </div>
                      ))}
                    </div>
                    {hunk.truncated && (
                      <div className="px-4 py-1 text-xs text-zinc-500 italic">
                        ... hunk truncated
                      </div>
                    )}
                  </div>
                ))
              ) : diff.diff ? (
                <pre className="px-4 py-2 text-xs text-zinc-400 whitespace-pre-wrap">{diff.diff}</pre>
              ) : (
                <div className="px-4 py-2 text-zinc-500 italic text-xs">No diff available</div>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
