"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import {
  fetchSpeculation,
  fetchSpeculationChanges,
  fetchSpeculationDiff,
  acceptSpeculation,
  clearSpeculation,
} from "@/lib/api";
import { buildSpeculationAcceptNotice } from "./chat-constants";
import type {
  SpeculationAcceptResult,
  SpeculationDiffEntry,
  SpeculationHunkSelection,
  SpeculationNotice,
  SpeculationRecord,
} from "@/types";

export function useSpeculation(activeThreadId: string | null) {
  const [speculationEnabled, setSpeculationEnabled] = useState(true);
  const [speculationRecord, setSpeculationRecord] = useState<SpeculationRecord | null>(null);
  const [speculationDiffs, setSpeculationDiffs] = useState<SpeculationDiffEntry[] | null>(null);
  const [speculationDiffLoading, setSpeculationDiffLoading] = useState(false);
  const [speculationBusyAction, setSpeculationBusyAction] = useState<"refresh" | "accept" | "discard" | null>(null);
  const [speculationNotice, setSpeculationNotice] = useState<SpeculationNotice | null>(null);
  const [workspaceRefreshToken, setWorkspaceRefreshToken] = useState(0);
  const speculationRecordRef = useRef<SpeculationRecord | null>(null);

  useEffect(() => {
    const savedSpec = localStorage.getItem("speculationEnabled");
    if (savedSpec !== null) setSpeculationEnabled(savedSpec === "true");
  }, []);

  useEffect(() => {
    speculationRecordRef.current = speculationRecord;
  }, [speculationRecord]);

  const loadSpeculation = useCallback(async (threadId: string, includeChanges: boolean = true) => {
    try {
      const [record, changes] = await Promise.all([
        fetchSpeculation(threadId),
        includeChanges ? fetchSpeculationChanges(threadId) : Promise.resolve(null),
      ]);
      if (!record || record.error) {
        const current = speculationRecordRef.current;
        if (current && current.thread_id === threadId && ["pending", "running"].includes(current.status)) {
          return current;
        }
        setSpeculationRecord(null);
        return null;
      }
      const nextRecord = record as SpeculationRecord;
      if (
        includeChanges
        && changes
        && !changes.error
        && nextRecord.status !== "accepted"
        && Array.isArray(changes.changes)
      ) {
        nextRecord.changes = changes.changes;
      }
      setSpeculationRecord(nextRecord);
      return nextRecord;
    } catch {
      const current = speculationRecordRef.current;
      if (current && current.thread_id === threadId && ["pending", "running"].includes(current.status)) {
        return current;
      }
      return null;
    }
  }, []);

  const loadSpeculationDiff = useCallback(async (threadId: string) => {
    setSpeculationDiffLoading(true);
    try {
      const data = await fetchSpeculationDiff(threadId);
      if (!data || data.error || !Array.isArray(data.diffs)) {
        setSpeculationDiffs([]);
        return [];
      }
      const diffs = data.diffs as SpeculationDiffEntry[];
      setSpeculationDiffs(diffs);
      return diffs;
    } catch {
      setSpeculationDiffs([]);
      return [];
    } finally {
      setSpeculationDiffLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!activeThreadId) {
      setSpeculationRecord(null);
      setSpeculationDiffs(null);
      return;
    }
    loadSpeculation(activeThreadId);
  }, [activeThreadId, loadSpeculation]);

  useEffect(() => {
    if (!speculationRecord) {
      setSpeculationDiffs(null);
      return;
    }
    setSpeculationDiffs(null);
  }, [speculationRecord?.thread_id, speculationRecord?.created_at]);

  useEffect(() => {
    if (!activeThreadId || !speculationRecord || speculationRecord.thread_id !== activeThreadId) {
      return;
    }
    if (!["pending", "running"].includes(speculationRecord.status)) {
      return;
    }
    const timer = window.setInterval(() => {
      loadSpeculation(activeThreadId);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [activeThreadId, speculationRecord, loadSpeculation]);

  useEffect(() => {
    if (!activeThreadId || !speculationRecord || speculationRecord.thread_id !== activeThreadId) {
      return;
    }
    if (!(speculationRecord.changes || []).length) {
      return;
    }
    if (!["completed", "consumed", "partially_accepted"].includes(speculationRecord.status)) {
      return;
    }
    if (speculationDiffs !== null) {
      return;
    }
    void loadSpeculationDiff(activeThreadId);
  }, [activeThreadId, speculationRecord, speculationDiffs, loadSpeculationDiff]);

  const handleRefreshSpeculation = useCallback(async () => {
    if (!activeThreadId) return;
    setSpeculationBusyAction("refresh");
    try {
      const record = await loadSpeculation(activeThreadId);
      if (!record) {
        setSpeculationNotice({ kind: "info", title: "未找到推测分支", detail: "当前线程还没有活动中的推测分支。" });
        setSpeculationDiffs([]);
        return;
      }
      if (record && record.status !== "accepted" && Array.isArray(record.changes) && record.changes.length > 0) {
        await loadSpeculationDiff(activeThreadId);
      } else {
        setSpeculationDiffs([]);
      }
      setSpeculationNotice({
        kind: "info",
        title: "推测分支已刷新",
        detail: (record.changes || []).length > 0 ? `当前有 ${(record.changes || []).length} 项变更可供审阅。` : "当前没有剩余的推测性变更。",
        remainingCount: (record.changes || []).length,
      });
    } finally {
      setSpeculationBusyAction(null);
    }
  }, [activeThreadId, loadSpeculation, loadSpeculationDiff]);

  const handleAcceptSpeculation = useCallback(async (selectedPaths?: string[], selectedHunks?: SpeculationHunkSelection[]) => {
    if (!activeThreadId) return;
    setSpeculationBusyAction("accept");
    try {
      const result = await acceptSpeculation(activeThreadId, selectedPaths, selectedHunks);
      if (result?.error) {
        setSpeculationNotice({ kind: "error", title: "接受失败", detail: result.error });
        await loadSpeculation(activeThreadId);
        return;
      }
      if (result?.accept_result?.error) {
        setSpeculationNotice({ kind: "error", title: "接受失败", detail: result.accept_result.error });
        await loadSpeculation(activeThreadId);
        return;
      }
      const nextRecord = result as SpeculationRecord;
      const acceptResult = result?.accept_result as SpeculationAcceptResult | undefined;
      setSpeculationRecord(nextRecord);
      setSpeculationNotice(buildSpeculationAcceptNotice(nextRecord, acceptResult));
      setWorkspaceRefreshToken((prev) => prev + 1);
      if (result?.status === "accepted" || result?.status === "partially_accepted") {
        if (Array.isArray(result?.changes) && result.changes.length > 0) {
          await loadSpeculationDiff(activeThreadId);
        } else {
          setSpeculationDiffs([]);
        }
      }
    } catch (err) {
      setSpeculationNotice({
        kind: "error",
        title: "接受推测分支失败",
        detail: err instanceof Error ? err.message : "未知错误",
      });
    } finally {
      setSpeculationBusyAction(null);
    }
  }, [activeThreadId, loadSpeculation, loadSpeculationDiff]);

  const handleDiscardSpeculation = useCallback(async () => {
    if (!activeThreadId) return;
    setSpeculationBusyAction("discard");
    try {
      const result = await clearSpeculation(activeThreadId);
      if (result?.error) {
        setSpeculationNotice({ kind: "error", title: "丢弃失败", detail: result.error });
        return;
      }
      setSpeculationRecord(null);
      setSpeculationDiffs(null);
      setSpeculationNotice({
        kind: "info",
        title: "推测分支已丢弃",
        detail: "推测分支及其待处理变更已经清除。",
      });
    } catch (err) {
      setSpeculationNotice({
        kind: "error",
        title: "丢弃推测分支失败",
        detail: err instanceof Error ? err.message : "未知错误",
      });
    } finally {
      setSpeculationBusyAction(null);
    }
  }, [activeThreadId]);

  const toggleSpeculation = useCallback(() => {
    const next = !speculationEnabled;
    setSpeculationEnabled(next);
    localStorage.setItem("speculationEnabled", String(next));
    if (!next) {
      setSpeculationRecord(null);
      setSpeculationDiffs(null);
      setSpeculationNotice(null);
    }
  }, [speculationEnabled]);

  const resetSpeculation = useCallback(() => {
    setSpeculationRecord(null);
    setSpeculationDiffs(null);
    setSpeculationBusyAction(null);
    setSpeculationNotice(null);
  }, []);

  return {
    speculationEnabled,
    speculationRecord,
    setSpeculationRecord,
    speculationDiffs,
    setSpeculationDiffs,
    speculationDiffLoading,
    speculationBusyAction,
    setSpeculationBusyAction,
    speculationNotice,
    setSpeculationNotice,
    workspaceRefreshToken,
    setWorkspaceRefreshToken,
    loadSpeculation,
    toggleSpeculation,
    handleRefreshSpeculation,
    handleAcceptSpeculation,
    handleDiscardSpeculation,
    resetSpeculation,
  };
}
