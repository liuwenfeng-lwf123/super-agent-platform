"use client";

import React from "react";
import { FileText, Image, FileCode, File, Download } from "lucide-react";

interface FileAttachmentProps {
  filename: string;
  size: number;
  url?: string;
}

function getFileIcon(filename: string) {
  const ext = filename.split(".").pop()?.toLowerCase() || "";
  if (["jpg", "jpeg", "png", "gif", "svg", "webp"].includes(ext)) return Image;
  if (["js", "ts", "py", "java", "go", "rs", "cpp", "c", "html", "css"].includes(ext)) return FileCode;
  if (["txt", "md", "pdf", "doc", "docx"].includes(ext)) return FileText;
  return File;
}

function formatSize(bytes: number) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

export function FileAttachment({ filename, size, url }: FileAttachmentProps) {
  const Icon = getFileIcon(filename);

  return (
    <div
      className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border text-xs transition-colors hover:opacity-80"
      style={{
        background: "var(--bg-secondary)",
        borderColor: "var(--border-color)",
        color: "var(--text-primary)",
      }}
    >
      <Icon className="w-4 h-4 flex-shrink-0" style={{ color: "var(--accent)" }} />
      <div className="min-w-0">
        <div className="font-medium truncate max-w-48">{filename}</div>
        <div style={{ color: "var(--text-secondary)" }}>{formatSize(size)}</div>
      </div>
      {url && (
        <a
          href={url}
          download={filename}
          className="ml-1 p-1 rounded hover:opacity-70"
          style={{ color: "var(--text-secondary)" }}
        >
          <Download className="w-3.5 h-3.5" />
        </a>
      )}
    </div>
  );
}

export function parseFileAttachments(content: string): {
  text: string;
  attachments: { filename: string; size: number }[];
} {
  const regex = /\[Attached file: ([^\]]+) \((\d+) bytes\)\]/g;
  const attachments: { filename: string; size: number }[] = [];
  let match;

  while ((match = regex.exec(content)) !== null) {
    attachments.push({ filename: match[1], size: parseInt(match[2]) });
  }

  const text = content.replace(regex, "").trim();
  return { text, attachments };
}
