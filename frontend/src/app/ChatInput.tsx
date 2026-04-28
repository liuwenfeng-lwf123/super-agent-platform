"use client";

import { useRef } from "react";
import {
  Send,
  Paperclip,
  StopCircle,
  ImageIcon,
  Wrench,
  Brain,
} from "lucide-react";
import { TOOL_NAME_LABELS, TOOL_CATEGORY_LABELS, fallbackToolLabel, fallbackToolSummary } from "./chat-constants";
import type { ChatTool } from "./chat-constants";
import type { SlashCommand } from "@/lib/slash";

interface ChatInputProps {
  input: string;
  setInput: (v: string) => void;
  pendingImages: string[];
  setPendingImages: React.Dispatch<React.SetStateAction<string[]>>;
  streaming: boolean;
  agentMode: string;
  slashCompletions: SlashCommand[];
  setSlashCompletions: (v: SlashCommand[]) => void;
  slashSelectedIdx: number;
  setSlashSelectedIdx: React.Dispatch<React.SetStateAction<number>>;
  getSlashCompletions: (val: string) => SlashCommand[];
  skills: { name: string; display_name: string; description: string }[];
  selectedSkillNames: string[];
  toggleSelectedSkill: (name: string) => void;
  setSelectedSkillNames: (v: string[]) => void;
  showSkillPanel: boolean;
  setShowSkillPanel: React.Dispatch<React.SetStateAction<boolean>>;
  availableTools: ChatTool[];
  visibleTools: ChatTool[];
  selectedToolNames: string[];
  toggleSelectedTool: (name: string) => void;
  setSelectedToolNames: (v: string[]) => void;
  showToolPanel: boolean;
  setShowToolPanel: React.Dispatch<React.SetStateAction<boolean>>;
  toolSearch: string;
  setToolSearch: (v: string) => void;
  handleSend: () => void;
  handleStop: () => void;
  handleKeyDown: (e: React.KeyboardEvent) => void;
  handleFileUpload: (e: React.ChangeEvent<HTMLInputElement>) => void;
  inputRef: React.RefObject<HTMLTextAreaElement>;
  fileInputRef: React.RefObject<HTMLInputElement>;
}

export function ChatInput({
  input,
  setInput,
  pendingImages,
  setPendingImages,
  streaming,
  agentMode,
  slashCompletions,
  setSlashCompletions,
  slashSelectedIdx,
  setSlashSelectedIdx,
  getSlashCompletions,
  skills,
  selectedSkillNames,
  toggleSelectedSkill,
  setSelectedSkillNames,
  showSkillPanel,
  setShowSkillPanel,
  availableTools,
  visibleTools,
  selectedToolNames,
  toggleSelectedTool,
  setSelectedToolNames,
  showToolPanel,
  setShowToolPanel,
  toolSearch,
  setToolSearch,
  handleSend,
  handleStop,
  handleKeyDown,
  handleFileUpload,
  inputRef,
  fileInputRef,
}: ChatInputProps) {
  return (
    <div className="px-6 pb-5">
      <div className="max-w-3xl mx-auto">
        {pendingImages.length > 0 && (
          <div className="flex gap-2 mb-2 flex-wrap">
            {pendingImages.map((img, idx) => (
              <div key={idx} className="relative group">
                <img src={img} alt="" className="w-16 h-16 rounded-lg object-cover border" style={{ borderColor: "var(--border-color)" }} />
                <button
                  onClick={() => setPendingImages((prev) => prev.filter((_, j) => j !== idx))}
                  className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full flex items-center justify-center text-white text-[10px] opacity-0 group-hover:opacity-100 transition-opacity"
                  style={{ background: "#ef4444" }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="relative flex items-end gap-2">
          <input ref={fileInputRef} type="file" className="hidden" onChange={handleFileUpload} />
          <input
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            id="image-input"
            onChange={(e) => {
              const files = e.target.files;
              if (!files) return;
              const MAX_IMAGE_SIZE = 5 * 1024 * 1024; // 5 MB
              Array.from(files).forEach((file) => {
                if (file.size > MAX_IMAGE_SIZE) {
                  alert(`图片 "${file.name}" 超过 5MB 限制，请压缩后重试`);
                  return;
                }
                const reader = new FileReader();
                reader.onload = () => {
                  if (typeof reader.result === "string") {
                    setPendingImages((prev) => [...prev, reader.result as string]);
                  }
                };
                reader.readAsDataURL(file);
              });
              e.target.value = "";
            }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            className="p-2.5 rounded-xl border flex-shrink-0"
            style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-secondary)" }}
            title="上传文件"
          >
            <Paperclip className="w-4 h-4" />
          </button>
          <button
            onClick={() => document.getElementById("image-input")?.click()}
            className="p-2.5 rounded-xl border flex-shrink-0"
            style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-secondary)" }}
            title="附加图片"
          >
            <ImageIcon className="w-4 h-4" />
          </button>
          <div className="relative flex-shrink-0">
            <button
              onClick={() => { setShowSkillPanel((value) => !value); setShowToolPanel(false); }}
              className="p-2.5 rounded-xl border flex items-center gap-1"
              style={{
                background: selectedSkillNames.length > 0 ? "rgba(124,58,237,0.14)" : "var(--bg-secondary)",
                borderColor: selectedSkillNames.length > 0 ? "var(--accent)" : "var(--border-color)",
                color: selectedSkillNames.length > 0 ? "var(--accent)" : "var(--text-secondary)",
              }}
              title="控制当前对话默认可用技能"
            >
              <Brain className="w-4 h-4" />
              {selectedSkillNames.length > 0 && <span className="text-xs font-semibold">{selectedSkillNames.length}</span>}
            </button>
            {showSkillPanel && (
              <div
                className="absolute bottom-full left-0 mb-2 w-80 rounded-xl border shadow-xl p-3 z-50"
                style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)" }}
              >
                <div className="flex items-start justify-between gap-3 mb-2">
                  <div>
                    <div className="text-sm font-semibold">当前对话技能</div>
                    <div className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
                      {selectedSkillNames.length === 0 ? "自动：由 AI 自己选择是否使用技能" : `已固定 ${selectedSkillNames.length} 个默认技能`}
                    </div>
                  </div>
                  <button onClick={() => setSelectedSkillNames([])} className="text-xs" style={{ color: "var(--text-secondary)" }}>自动</button>
                </div>
                <div className="max-h-64 overflow-auto space-y-1">
                  {skills.length === 0 ? (
                    <div className="text-xs py-6 text-center" style={{ color: "var(--text-secondary)" }}>暂无技能</div>
                  ) : skills.map((skill) => {
                    const checked = selectedSkillNames.includes(skill.name);
                    return (
                      <button
                        key={skill.name}
                        onClick={() => toggleSelectedSkill(skill.name)}
                        className="w-full text-left px-2 py-2 rounded-lg border transition-colors"
                        style={{
                          background: checked ? "rgba(124,58,237,0.14)" : "transparent",
                          borderColor: checked ? "var(--accent)" : "transparent",
                          color: "var(--text-primary)",
                        }}
                      >
                        <div className="flex items-center gap-2">
                          <span className={`w-3.5 h-3.5 rounded border flex-shrink-0 ${checked ? "bg-purple-500 border-purple-500" : "border-gray-500"}`} />
                          <div className="min-w-0">
                            <div className="text-sm font-medium truncate">{skill.display_name}</div>
                            <div className="text-xs truncate" style={{ color: "var(--text-secondary)" }}>{skill.description}</div>
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
                <div className="text-[11px] mt-2" style={{ color: "var(--text-secondary)" }}>
                  选择后会作为这个对话每次发送的默认技能；点"自动"恢复不限制。
                </div>
              </div>
            )}
          </div>
          <div className="relative flex-shrink-0">
            <button
              onClick={() => { setShowToolPanel((value) => !value); setShowSkillPanel(false); }}
              className="p-2.5 rounded-xl border flex items-center gap-1"
              style={{
                background: selectedToolNames.length > 0 ? "rgba(59,130,246,0.14)" : "var(--bg-secondary)",
                borderColor: selectedToolNames.length > 0 ? "#3b82f6" : "var(--border-color)",
                color: selectedToolNames.length > 0 ? "#60a5fa" : "var(--text-secondary)",
              }}
              title="控制当前对话默认可用工具"
            >
              <Wrench className="w-4 h-4" />
              {selectedToolNames.length > 0 && <span className="text-xs font-semibold">{selectedToolNames.length}</span>}
            </button>
            {showToolPanel && (
              <div
                className="absolute bottom-full left-0 mb-2 w-96 rounded-xl border shadow-xl p-3 z-50"
                style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)" }}
              >
                <div className="flex items-start justify-between gap-3 mb-2">
                  <div>
                    <div className="text-sm font-semibold">当前对话工具</div>
                    <div className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
                      {selectedToolNames.length === 0 ? "自动：AI 可按需要选择工具" : `只允许使用 ${selectedToolNames.length} 个工具`}
                    </div>
                  </div>
                  <button onClick={() => setSelectedToolNames([])} className="text-xs" style={{ color: "var(--text-secondary)" }}>自动</button>
                </div>
                <input
                  value={toolSearch}
                  onChange={(event) => setToolSearch(event.target.value)}
                  placeholder="搜索工具，例如 网页、文件、Python"
                  className="w-full px-3 py-2 mb-2 rounded-lg border text-sm outline-none"
                  style={{ background: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                />
                <div className="max-h-72 overflow-auto space-y-1">
                  {visibleTools.length === 0 ? (
                    <div className="text-xs py-6 text-center" style={{ color: "var(--text-secondary)" }}>没有匹配工具</div>
                  ) : visibleTools.map((tool) => {
                    const checked = selectedToolNames.includes(tool.name);
                    const category = tool.category || "general";
                    const riskText = tool.risk === "high" ? "高风险" : tool.risk === "medium" ? "中风险" : "低风险";
                    const riskColor = tool.risk === "high" ? "text-red-300 bg-red-500/15" : tool.risk === "medium" ? "text-yellow-300 bg-yellow-500/15" : "text-green-300 bg-green-500/15";
                    return (
                      <button
                        key={tool.name}
                        onClick={() => toggleSelectedTool(tool.name)}
                        className="w-full text-left px-2 py-2 rounded-lg border transition-colors"
                        style={{
                          background: checked ? "rgba(59,130,246,0.14)" : "transparent",
                          borderColor: checked ? "#3b82f6" : "transparent",
                          color: "var(--text-primary)",
                        }}
                      >
                        <div className="flex items-start gap-2">
                          <span className={`mt-0.5 w-3.5 h-3.5 rounded border flex-shrink-0 ${checked ? "bg-blue-500 border-blue-500" : "border-gray-500"}`} />
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <div className="text-sm font-medium truncate">{tool.display_name || TOOL_NAME_LABELS[tool.name] || fallbackToolLabel(tool.name)}</div>
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-300">{TOOL_CATEGORY_LABELS[category] || category}</span>
                              <span className={`text-[10px] px-1.5 py-0.5 rounded ${riskColor}`}>{riskText}</span>
                            </div>
                            <div className="text-xs truncate" style={{ color: "var(--text-secondary)" }}>{fallbackToolSummary(tool)}</div>
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
                <div className="text-[11px] mt-2" style={{ color: "var(--text-secondary)" }}>
                  不选择表示自动；选择后，本对话只允许这些工具被 AI 调用。
                </div>
              </div>
            )}
          </div>
          <div className="flex-1 relative">
            {slashCompletions.length > 0 && (
              <div
                className="absolute bottom-full left-0 mb-2 rounded-lg border shadow-lg overflow-hidden z-50 max-h-64 overflow-y-auto"
                style={{ background: "var(--bg-primary)", borderColor: "var(--border-color)", minWidth: "260px" }}
              >
                {slashCompletions.map((c, i) => (
                  <div
                    key={c.name + i}
                    className={`px-3 py-2 cursor-pointer text-sm ${i === slashSelectedIdx ? "" : "hover:opacity-80"}`}
                    style={i === slashSelectedIdx
                      ? { background: "var(--accent)", color: "#fff" }
                      : { color: "var(--text-primary)" }}
                    onMouseEnter={() => setSlashSelectedIdx(i)}
                    onClick={() => {
                      setInput(`/${c.name} `);
                      setSlashCompletions([]);
                      inputRef.current?.focus();
                    }}
                  >
                    <div className="font-mono font-semibold">/{c.name}</div>
                    <div className="text-xs opacity-80">{c.description}</div>
                  </div>
                ))}
              </div>
            )}
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => {
                const val = e.target.value;
                setInput(val);
                const completions = val.startsWith("/") && !val.includes(" ")
                  ? getSlashCompletions(val)
                  : [];
                setSlashCompletions(completions);
                setSlashSelectedIdx(0);
              }}
              onKeyDown={(e) => {
                if (slashCompletions.length > 0) {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    setSlashSelectedIdx(i => Math.min(slashCompletions.length - 1, i + 1));
                    return;
                  }
                  if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setSlashSelectedIdx(i => Math.max(0, i - 1));
                    return;
                  }
                  if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
                    e.preventDefault();
                    const picked = slashCompletions[slashSelectedIdx];
                    if (picked) {
                      setInput(`/${picked.name} `);
                      setSlashCompletions([]);
                    }
                    return;
                  }
                  if (e.key === "Escape") {
                    e.preventDefault();
                    setSlashCompletions([]);
                    return;
                  }
                }
                handleKeyDown(e);
              }}
              onPaste={(e) => {
                const items = e.clipboardData?.items;
                if (!items) return;
                for (const item of Array.from(items)) {
                  if (item.type.startsWith("image/")) {
                    e.preventDefault();
                    const file = item.getAsFile();
                    if (!file) continue;
                    const reader = new FileReader();
                    reader.onload = () => {
                      if (typeof reader.result === "string") {
                        setPendingImages((prev) => [...prev, reader.result as string]);
                      }
                    };
                    reader.readAsDataURL(file);
                  }
                }
              }}
              placeholder={agentMode === "local" ? "本地模式：让 AI 直接操作你的电脑..." : `发送消息... 例如\u201c搜索 AI 新闻\u201d或直接粘贴图片`}
              rows={1}
              className="w-full pl-4 pr-12 py-3 rounded-xl border text-sm resize-none outline-none focus:ring-2 transition-shadow"
              style={{
                background: "var(--bg-secondary)",
                borderColor: "var(--border-color)",
                color: "var(--text-primary)",
                minHeight: "48px",
                maxHeight: "160px",
              }}
              onInput={(e) => {
                const el = e.target as HTMLTextAreaElement;
                el.style.height = "48px";
                el.style.height = Math.min(el.scrollHeight, 160) + "px";
              }}
            />
            <button
              onClick={streaming ? handleStop : handleSend}
              disabled={!streaming && !input.trim() && pendingImages.length === 0}
              className="absolute right-3 top-1/2 -translate-y-1/2 p-1.5 rounded-lg disabled:opacity-30 transition-opacity"
              style={{ background: "var(--accent)", color: "#fff" }}
            >
              {streaming ? <StopCircle className="w-4 h-4" /> : <Send className="w-4 h-4" />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
