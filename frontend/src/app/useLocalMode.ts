"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  bindLocalThread,
  fetchLocalAuditLog,
  fetchLocalClients,
  fetchLocalSchedules,
  fetchLocalShortcuts,
  fetchLocalToolStats,
  setLocalAutoApprove,
  setLocalToolPermission,
} from "@/lib/api";
import type { LocalSchedule } from "@/lib/api";
import type { LocalClient } from "@/types";

interface UseLocalModeOptions {
  activeThreadId: string | null;
  setActiveThreadId: (id: string) => void;
  loadThreads: () => Promise<void>;
}

export function useLocalMode({ activeThreadId, setActiveThreadId, loadThreads }: UseLocalModeOptions) {
  const [localClients, setLocalClients] = useState<LocalClient[]>([]);
  const [localAuditLog, setLocalAuditLog] = useState<{timestamp: number; action: string; params_summary: Record<string, string>; success: boolean}[]>([]);
  const [toolStats, setToolStats] = useState<{ tool: string; total: number; success: number }[]>([]);
  const [localShortcuts, setLocalShortcuts] = useState<{ name: string; description: string; steps: string[] }[]>([]);
  const [localSchedules, setLocalSchedules] = useState<LocalSchedule[]>([]);
  const [showLocalPanel, setShowLocalPanel] = useState(false);
  const [localDisconnectNotice, setLocalDisconnectNotice] = useState(false);
  const [localPanelMessage, setLocalPanelMessage] = useState("");
  const prevLocalClientCount = useRef(0);

  const loadLocalClients = useCallback(async () => {
    try {
      const data = await fetchLocalClients();
      setLocalClients(data);
    } catch {}
  }, []);

  const loadLocalAuditLog = useCallback(async () => {
    try {
      const data = await fetchLocalAuditLog(30);
      setLocalAuditLog(Array.isArray(data) ? data : []);
    } catch {}
  }, []);

  const loadToolStats = useCallback(async () => {
    try {
      const data = await fetchLocalToolStats();
      setToolStats(Array.isArray(data) ? data : []);
    } catch {}
  }, []);

  const loadShortcuts = useCallback(async () => {
    try {
      const data = await fetchLocalShortcuts();
      setLocalShortcuts(Array.isArray(data) ? data : []);
    } catch {}
  }, []);

  const loadSchedules = useCallback(async () => {
    try {
      const data = await fetchLocalSchedules();
      setLocalSchedules(Array.isArray(data) ? data : []);
    } catch {}
  }, []);

  useEffect(() => {
    const interval = setInterval(() => { loadLocalClients(); loadLocalAuditLog(); loadToolStats(); }, 5000);
    loadLocalClients();
    loadLocalAuditLog();
    loadToolStats();
    loadShortcuts();
    loadSchedules();
    return () => clearInterval(interval);
  }, [loadLocalClients, loadLocalAuditLog, loadToolStats, loadShortcuts, loadSchedules]);

  useEffect(() => {
    if (prevLocalClientCount.current > 0 && localClients.length === 0) {
      setLocalDisconnectNotice(true);
      setTimeout(() => setLocalDisconnectNotice(false), 8000);
    }
    prevLocalClientCount.current = localClients.length;
  }, [localClients]);

  const ensureThreadForLocalBinding = useCallback(async () => {
    if (activeThreadId) return activeThreadId;
    const res = await fetch("/api/threads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "本地模式会话" }),
    });
    const thread = await res.json();
    if (thread?.id) {
      setActiveThreadId(thread.id);
      await loadThreads();
      return thread.id as string;
    }
    throw new Error("创建本地模式会话失败");
  }, [activeThreadId, setActiveThreadId, loadThreads]);

  const handleBindLocalThread = useCallback(async (clientId: string) => {
    setLocalPanelMessage("");
    try {
      const threadId = await ensureThreadForLocalBinding();
      await bindLocalThread(threadId, clientId);
      setLocalPanelMessage("已绑定到当前线程，可以直接在本地模式发送消息。");
    } catch (error) {
      setLocalPanelMessage(error instanceof Error ? error.message : "绑定失败");
    }
  }, [ensureThreadForLocalBinding]);

  const handleSetLocalAutoApprove = useCallback(async (clientId: string, enabled: boolean) => {
    await setLocalAutoApprove(clientId, enabled);
    await loadLocalClients();
  }, [loadLocalClients]);

  const handleSetLocalToolPermission = useCallback(async (clientId: string, tool: string, enabled: boolean) => {
    await setLocalToolPermission(clientId, tool, enabled);
    await loadLocalClients();
  }, [loadLocalClients]);

  return {
    localClients,
    localAuditLog,
    toolStats,
    localShortcuts,
    localSchedules,
    showLocalPanel,
    setShowLocalPanel,
    localDisconnectNotice,
    setLocalDisconnectNotice,
    localPanelMessage,
    loadLocalClients,
    loadLocalAuditLog,
    loadToolStats,
    loadShortcuts,
    loadSchedules,
    handleBindLocalThread,
    handleSetLocalAutoApprove,
    handleSetLocalToolPermission,
  };
}
