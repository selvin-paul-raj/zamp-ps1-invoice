import "./globals.css";
import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = { title: "Invoice → PO Decisions", description: "Zamp PS-1 case study: invoice to PO match decisions" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans antialiased">
        <header className="border-b border-border bg-white">
          <div className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-3">
            <Link href="/" className="text-sm font-semibold">Invoice → PO Decisions</Link>
            <nav className="flex gap-4 text-sm text-muted-foreground">
              <Link href="/" className="hover:text-foreground">Run history</Link>
              <Link href="/review/" className="hover:text-foreground">Review queue</Link>
            </nav>
            <span className="ml-auto text-xs text-muted-foreground">rules-v2 · code decides, AI reads · synthetic data</span>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-6 py-6">{children}</main>
      </body>
    </html>
  );
}
