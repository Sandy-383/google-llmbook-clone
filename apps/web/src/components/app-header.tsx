"use client";

import Link from "next/link";
import type { User } from "@/lib/api";
import { useLogout } from "@/lib/queries";

export function AppHeader({ user, children }: { user: User; children?: React.ReactNode }) {
  const logout = useLogout();
  return (
    <header className="border-b border-border bg-card">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4 sm:px-6">
        <Link href="/" className="shrink-0 font-semibold tracking-tight">
          LLMBook
        </Link>
        <div className="min-w-0 flex-1">{children}</div>
        <span className="hidden truncate text-sm text-muted sm:inline">
          {user.display_name || user.email}
        </span>
        <button
          onClick={() => logout.mutate()}
          disabled={logout.isPending}
          className="shrink-0 rounded-lg px-3 py-1.5 text-sm text-muted transition hover:bg-background hover:text-foreground"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}

export function FullPageMessage({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex flex-1 items-center justify-center p-6 text-sm text-muted">
      {children}
    </main>
  );
}
