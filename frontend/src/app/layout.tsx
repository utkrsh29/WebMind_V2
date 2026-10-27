import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "WebMind — Autonomous AI Web Agent",
  description:
    "Failure-aware autonomous AI web agent with deterministic task-aware multi-agent orchestration and live browser verification.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased font-sans selection:bg-teal-500/30 selection:text-teal-200">
        {children}
      </body>
    </html>
  );
}
