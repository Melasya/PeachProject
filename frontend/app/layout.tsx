import type { Metadata } from "next";
import { Geist_Mono, Inter } from "next/font/google";

import { Providers } from "@/components/providers";
import { SiteHeader } from "@/components/site-header";
import { Toaster } from "@/components/ui/sonner";

import "./globals.css";

const inter = Inter({
  variable: "--font-notion-sans",
  subsets: ["latin"],
  display: "swap",
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Peach",
  description: "FastAPI + Next.js + Postgres starter",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${geistMono.variable} h-full antialiased`}
    >
      {/* Extensions such as Grammarly stamp attributes on <body> before React
          hydrates; this silences that one-level mismatch only. */}
      <body className="flex min-h-full flex-col" suppressHydrationWarning>
        <Providers>
          <SiteHeader />
          <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-12 sm:px-8">
            {children}
          </main>
          <footer className="mt-auto">
            <div className="mx-auto w-full max-w-5xl px-6 py-8 text-xs text-muted-foreground sm:px-8">
              Peach - FastAPI, Next.js and Postgres, wired together.
            </div>
          </footer>
          <Toaster />
        </Providers>
      </body>
    </html>
  );
}
