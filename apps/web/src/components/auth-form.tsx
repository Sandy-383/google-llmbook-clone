"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FormEvent } from "react";
import { useAuth, useAuthConfig } from "@/lib/queries";

const copy = {
  login: {
    title: "Sign in",
    submit: "Sign in",
    switchText: "New here?",
    switchLink: "Create an account",
    switchHref: "/register",
  },
  register: {
    title: "Create your account",
    submit: "Create account",
    switchText: "Already have an account?",
    switchLink: "Sign in",
    switchHref: "/login",
  },
} as const;

const inputClass =
  "w-full rounded-lg border border-border bg-card px-3 py-2 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/30";

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const auth = useAuth(mode);
  const config = useAuthConfig(mode === "register");
  const registration = config.data?.registration;
  const text = copy[mode];

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    auth.mutate(
      {
        email: String(form.get("email")),
        password: String(form.get("password")),
        ...(mode === "register" && {
          display_name: String(form.get("display_name")) || null,
          invite_code: String(form.get("invite_code") ?? "") || undefined,
        }),
      },
      { onSuccess: () => router.replace("/") },
    );
  }

  return (
    <main className="flex flex-1 items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <p className="mb-8 text-center text-lg font-semibold tracking-tight">LLMBook</p>
        <form
          onSubmit={onSubmit}
          className="space-y-4 rounded-2xl border border-border bg-card p-6 shadow-sm"
        >
          <h1 className="text-xl font-semibold">{text.title}</h1>

          {registration === "closed" && (
            <p className="text-sm text-muted">
              Sign-up is closed on this server. Ask its owner to create an account for you.
            </p>
          )}
          {registration === "invite" && (
            <label className="block space-y-1 text-sm">
              <span className="text-muted">Invite code</span>
              <input name="invite_code" required autoComplete="off" className={inputClass} />
              <span className="block text-xs text-muted">Ask the person who shared this link.</span>
            </label>
          )}

          {mode === "register" && (
            <label className="block space-y-1 text-sm">
              <span className="text-muted">Name (optional)</span>
              <input name="display_name" autoComplete="name" maxLength={100} className={inputClass} />
            </label>
          )}
          <label className="block space-y-1 text-sm">
            <span className="text-muted">Email</span>
            <input name="email" type="email" required autoComplete="email" className={inputClass} />
          </label>
          <label className="block space-y-1 text-sm">
            <span className="text-muted">Password</span>
            <input
              name="password"
              type="password"
              required
              minLength={mode === "register" ? 10 : 1}
              maxLength={128}
              autoComplete={mode === "register" ? "new-password" : "current-password"}
              className={inputClass}
            />
            {mode === "register" && (
              <span className="block text-xs text-muted">At least 10 characters.</span>
            )}
          </label>

          {auth.error && (
            <p role="alert" className="text-sm text-danger">
              {auth.error.message}
            </p>
          )}

          <button
            type="submit"
            disabled={auth.isPending || registration === "closed"}
            className="w-full rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground transition hover:opacity-90 disabled:opacity-60"
          >
            {auth.isPending ? "Please wait…" : text.submit}
          </button>
        </form>
        <p className="mt-6 text-center text-sm text-muted">
          {text.switchText}{" "}
          <Link href={text.switchHref} className="font-medium text-accent hover:underline">
            {text.switchLink}
          </Link>
        </p>
        <p className="mt-3 text-center text-sm">
          <Link href="/get-app" className="text-muted hover:text-foreground hover:underline">
            Get the Android app
          </Link>
        </p>
      </div>
    </main>
  );
}
