"use client";

import SettingsPanel from "@/components/SettingsPanel";

export default function SettingsPage() {
  return <SettingsPanel onBack={() => window.history.back()} />;
}
