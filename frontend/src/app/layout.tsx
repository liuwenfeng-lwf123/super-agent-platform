import type { Metadata } from "next";
import "highlight.js/styles/github-dark.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Super Agent Platform",
  description: "AI Super Agent - Research, Code, Create",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" className="dark">
      <body className="h-screen overflow-hidden">{children}</body>
    </html>
  );
}
