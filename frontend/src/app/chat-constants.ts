import type { ModelConfig, SpeculationAcceptResult, SpeculationNotice, SpeculationRecord } from "@/types";

export const AGENT_MODE_META = {
  flash: { label: "极速", desc: "快速对话，无工具" },
  standard: { label: "标准", desc: "工具 + 工作区" },
  pro: { label: "专业", desc: "先规划再执行" },
  ultra: { label: "旗舰", desc: "多智能体并行" },
  local: { label: "本地", desc: "直接操作你的电脑" },
} as const;

export const STABLE_DEFAULT_MODEL = "Qwen/Qwen3-Coder-30B-A3B-Instruct";

export type ChatTool = {
  name: string;
  display_name?: string;
  summary?: string;
  description?: string;
  category?: string;
  risk?: "low" | "medium" | "high" | string;
  built_in?: boolean;
};

export type PermissionPrompt = {
  request_id: string;
  thread_id?: string;
  tool: string;
  input?: string;
  reason?: string;
  status?: string;
};

export const TOOL_NAME_LABELS: Record<string, string> = {
  web_search: "网页搜索",
  web_fetch: "读取网页",
  summarize_url: "总结网页",
  read_file: "读取文件",
  list_files: "列出文件",
  write_file: "写入文件",
  file_history: "查看文件历史",
  get_editor_state: "查看编辑器状态",
  get_editor_diagnostics: "查看代码诊断",
  execute_python: "运行 Python",
  execute_javascript: "运行 JavaScript",
  execute_bash: "运行终端命令",
  execute_code: "执行工具脚本",
  execute_tool_chain: "批量执行工具",
  calculate: "计算",
  get_current_time: "获取时间",
  remember: "保存记忆",
  create_skill: "创建技能",
  patch_skill: "修改技能片段",
  edit_skill: "完整编辑技能",
  rollback_skill: "回滚技能",
  list_custom_skills: "查看自定义技能",
  view_evolution_log: "查看进化记录",
  view_skill: "查看技能详情",
  score_skill: "评估技能效果",
  write_skill_file: "保存技能文件",
  remove_skill_file: "删除技能文件",
  record_skill_feedback: "记录技能反馈",
  gepa_evolve: "自动优化技能",
  create_tool: "创建工具",
  list_custom_tools: "查看自定义工具",
  remove_custom_tool: "删除自定义工具",
  tool_search: "查找工具",
  run_discovered_tool: "运行发现的工具",
  screenshot: "截取屏幕",
  clipboard_read: "读取剪贴板",
  clipboard_write: "写入剪贴板",
  system_info: "查看系统信息",
  open_app: "打开应用",
  open_url: "打开网页链接",
  browser_open: "打开浏览器页面",
  browser_get_state: "查看浏览器状态",
  browser_run_javascript: "浏览器执行脚本",
  browser_click: "点击网页元素",
  browser_fill: "填写网页表单",
  browser_extract_text: "提取网页文本",
  notify: "发送系统通知",
  git_command: "执行 Git 命令",
  http_request: "发送网络请求",
  pdf_extract: "读取 PDF 文本",
  knowledge_search: "搜索知识库",
  session_search: "搜索历史会话",
  spawn_agent: "启动子智能体",
  send_agent_message: "发送子智能体消息",
  register_hook: "注册自动钩子",
  execute_code_tool: "执行代码工具",
  elicit_input: "向用户提问",
  goto_definition: "跳转代码定义",
  find_references: "查找代码引用",
  document_symbols: "查看文件符号",
  call_hierarchy: "查看调用层级",
  get_ip: "查询公网 IP",
  local_read_file: "读取本地文件",
  local_write_file: "写入本地文件",
  local_list_files: "查看本地文件",
  local_execute_bash: "本地终端命令",
  local_execute_python: "本地 Python",
  local_open_app: "打开本地应用",
  local_get_system_info: "本地系统信息",
  local_upload_to_workspace: "上传本地文件到工作区",
  local_download_from_workspace: "保存工作区文件到本地",
  local_read_clipboard: "读取剪贴板",
  local_write_clipboard: "写入剪贴板",
  local_edit_file: "精准编辑文件",
  local_search_code: "代码搜索",
  local_git: "Git 操作",
  local_project_index: "项目索引",
  local_undo_edit: "撤销编辑",
  local_send_notification: "发送通知",
  local_manage_window: "管理窗口",
  local_create_schedule: "创建定时任务",
  local_list_schedules: "查看定时任务",
  local_delete_schedule: "删除定时任务",
};

export const LOCAL_PERMISSION_OPTIONS = [
  { tool: "local_open_app", label: "打开应用", desc: "Terminal、浏览器、系统应用" },
  { tool: "local_list_files", label: "查看文件列表", desc: "列出目录内容" },
  { tool: "local_read_file", label: "读取文件", desc: "查看本机文件内容" },
  { tool: "local_write_file", label: "写入文件", desc: "创建或修改本机文件" },
  { tool: "local_execute_bash", label: "终端命令", desc: "执行 shell 命令" },
  { tool: "local_execute_python", label: "Python", desc: "执行 Python 代码" },
  { tool: "local_get_system_info", label: "系统信息", desc: "读取设备和系统状态" },
  { tool: "local_upload_to_workspace", label: "上传文件", desc: "把本地文件上传到工作区" },
  { tool: "local_download_from_workspace", label: "保存文件", desc: "把工作区文件保存到本地" },
  { tool: "browser_open", label: "打开网页", desc: "用浏览器打开链接" },
  { tool: "screenshot", label: "截图", desc: "截取屏幕或网页截图" },
  { tool: "local_read_clipboard", label: "读取剪贴板", desc: "读取你复制的内容" },
  { tool: "local_write_clipboard", label: "写入剪贴板", desc: "把内容复制到剪贴板" },
  { tool: "local_edit_file", label: "精准编辑", desc: "按行精确修改代码文件" },
  { tool: "local_search_code", label: "代码搜索", desc: "在项目中搜索代码" },
  { tool: "local_git", label: "Git", desc: "执行 git 命令" },
  { tool: "local_project_index", label: "项目索引", desc: "分析项目结构" },
  { tool: "local_undo_edit", label: "撤销编辑", desc: "恢复文件到上一个版本" },
  { tool: "local_send_notification", label: "发送通知", desc: "发送系统通知" },
  { tool: "local_manage_window", label: "管理窗口", desc: "控制应用窗口" },
  { tool: "local_create_schedule", label: "创建定时任务", desc: "设置定时执行" },
  { tool: "local_list_schedules", label: "查看定时任务", desc: "查看现有任务" },
  { tool: "local_delete_schedule", label: "删除定时任务", desc: "移除定时任务" },
] as const;

export const TOOL_CATEGORY_LABELS: Record<string, string> = {
  search: "搜索",
  file: "文件",
  execution: "执行",
  utility: "实用",
  memory: "记忆",
  local: "本地",
  system: "系统",
  evolution: "进化",
  agents: "智能体",
  general: "通用",
};

export function fallbackToolLabel(name: string) {
  const words = name.split("_");
  const dictionary: Record<string, string> = {
    local: "本地",
    web: "网页",
    execute: "运行",
    read: "读取",
    write: "写入",
    list: "查看",
    remove: "删除",
    create: "创建",
    edit: "编辑",
    patch: "修改",
    rollback: "回滚",
    record: "记录",
    view: "查看",
    score: "评估",
    skill: "技能",
    tool: "工具",
    file: "文件",
    files: "文件",
    feedback: "反馈",
    history: "历史",
    evolve: "优化",
    custom: "自定义",
    search: "搜索",
    fetch: "读取",
    bash: "终端命令",
    python: "Python",
    javascript: "JavaScript",
    url: "网页链接",
    current: "当前",
    time: "时间",
    memory: "记忆",
    discovered: "发现的",
    gepa: "自动提示词优化",
    editor: "编辑器",
    diagnostics: "诊断",
    screenshot: "截屏",
    clipboard: "剪贴板",
    system: "系统",
    info: "信息",
    open: "打开",
    app: "应用",
    browser: "浏览器",
    state: "状态",
    run: "执行",
    click: "点击",
    fill: "填写",
    extract: "提取",
    text: "文本",
    notify: "通知",
    git: "Git",
    command: "命令",
    http: "HTTP",
    request: "请求",
    pdf: "PDF",
    knowledge: "知识库",
    session: "会话",
    spawn: "启动",
    agent: "智能体",
    send: "发送",
    message: "消息",
    register: "注册",
    hook: "钩子",
    elicit: "询问",
    input: "输入",
    goto: "跳转",
    definition: "定义",
    find: "查找",
    references: "引用",
    document: "文档",
    symbols: "符号",
    call: "调用",
    hierarchy: "层级",
    ip: "IP",
  };
  const translated = words.map((word) => dictionary[word] || "").filter(Boolean);
  return translated.length > 0 ? translated.join("") : "高级工具";
}

export function fallbackToolSummary(tool: ChatTool) {
  if (tool.summary) return tool.summary;
  const label = fallbackToolLabel(tool.name);
  if (tool.name.includes("skill")) return `用于管理或优化技能：${label}。`;
  if (tool.name.includes("file")) return `用于查看、读取或修改文件：${label}。`;
  if (tool.name.includes("web")) return `用于联网搜索或读取网页内容：${label}。`;
  if (tool.name.includes("local")) return `用于操作本地电脑资源：${label}。`;
  if (tool.name.includes("browser")) return `用于浏览器页面操作：${label}。`;
  if (tool.name.includes("editor") || tool.name.includes("diagnostics")) return `用于读取 IDE 编辑器上下文：${label}。`;
  if (tool.name.includes("agent")) return `用于启动或管理子智能体：${label}。`;
  if (tool.name.includes("knowledge")) return `用于搜索知识库内容：${label}。`;
  if (tool.name.includes("session")) return `用于搜索历史会话上下文：${label}。`;
  if (tool.name.includes("git")) return `用于执行版本控制操作：${label}。`;
  if (tool.name.includes("http")) return `用于调用网络接口：${label}。`;
  if (tool.name.includes("pdf")) return `用于读取 PDF 文档：${label}。`;
  if (tool.name.includes("execute")) return `用于运行代码或命令：${label}。`;
  return `用于扩展 AI 能力：${label}。`;
}

export function buildSpeculationAcceptNotice(record: SpeculationRecord, acceptResult?: SpeculationAcceptResult): SpeculationNotice {
  const applied = Array.isArray(acceptResult?.applied) ? acceptResult.applied : [];
  const appliedHunkCount = applied.reduce((total, item) => total + (item.hunks?.length || 0), 0);
  const remainingCount = Array.isArray(record.changes) ? record.changes.length : 0;
  const detailParts = [`已应用 ${applied.length} 项变更`];
  if (appliedHunkCount > 0) {
    detailParts.push(`包含 ${appliedHunkCount} 个代码块`);
  }
  if (remainingCount > 0) {
    detailParts.push(`分支中还剩 ${remainingCount} 项变更`);
  } else {
    detailParts.push("没有剩余的推测性变更");
  }
  return {
    kind: record.status === "accepted" ? "success" : "info",
    title: record.status === "accepted" ? "推测分支已应用到工作区" : "推测分支已部分应用",
    detail: detailParts.join("，") + "。",
    applied,
    remainingCount,
  };
}

export function standaloneNoticeStyle(kind: SpeculationNotice["kind"]) {
  if (kind === "success") {
    return { background: "rgba(34,197,94,0.10)", borderColor: "rgba(34,197,94,0.30)", color: "#22c55e" };
  }
  if (kind === "error") {
    return { background: "rgba(239,68,68,0.10)", borderColor: "rgba(239,68,68,0.30)", color: "#ef4444" };
  }
  return { background: "rgba(99,102,241,0.10)", borderColor: "rgba(99,102,241,0.22)", color: "var(--accent)" };
}

export function pickPreferredModel(modelList: ModelConfig[]): string {
  if (modelList.length === 0) return "";
  const keyedStable = modelList.find((model) => model.name === STABLE_DEFAULT_MODEL && model.has_api_key);
  if (keyedStable) return keyedStable.name;
  const keyed = modelList.find((model) => model.has_api_key);
  if (keyed) return keyed.name;
  const stable = modelList.find((model) => model.name === STABLE_DEFAULT_MODEL);
  return (stable || modelList[0]).name;
}

export function buildChatErrorMessage(rawError: string, selectedModel: string, modelList: ModelConfig[]): string {
  const normalized = rawError.trim();
  const fallback = modelList.find((model) => model.name !== selectedModel);
  if (normalized.includes("429") || /rate limit/i.test(normalized)) {
    const suggestion = fallback
      ? `建议先切换到"${fallback.display_name}"后重试。`
      : "建议稍后重试，或在模型下拉框里切换到其他可用模型。";
    return `当前模型触发了提供方限流（429）。这通常不是你的请求内容有问题，而是该模型当前配额紧张。${suggestion}`;
  }
  return normalized;
}
