import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "语音 AI 助理 Demo",
  description: "LiveKit + Inworld few-shot 注入 demo",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full flex flex-col font-sans">{children}</body>
    </html>
  );
}
