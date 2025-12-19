"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import "./globals.css";

export default function RootLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const isWideLayout = pathname?.startsWith("/passport-tuning");
  
  return (
    <html lang="ru">
      <body className="min-h-screen bg-slate-50 text-slate-900">
        <header className="border-b bg-white">
          <nav className={`mx-auto flex items-center justify-between px-4 py-3 ${isWideLayout ? "max-w-screen-2xl" : "max-w-5xl"}`}>
            <div className="font-semibold">ClinNexus MVP</div>
            <div className="flex gap-4 text-sm">
              <Link href="/studies">Исследования</Link>
            </div>
          </nav>
        </header>
        <main className={isWideLayout ? "w-full" : "mx-auto max-w-5xl px-4 py-6"}>{children}</main>
      </body>
    </html>
  );
}


