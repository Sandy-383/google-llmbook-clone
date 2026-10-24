import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Get the Android app · LLMBook" };

const STEPS = [
  "Tap Download and open the file when it finishes (it's about 1 MB).",
  "If Android asks, allow your browser to install apps: tap Settings, switch on “Allow from this source”, then go back.",
  "Tap Install. Android may warn that the app isn't from the Play Store. That's expected for an app shared directly. Tap Install anyway (or More details → Install anyway).",
  "Open LLMBook and sign in with your account.",
];

export default function GetAppPage() {
  return (
    <main className="flex flex-1 items-center justify-center px-4 py-12">
      <div className="w-full max-w-md text-center">
        {/* eslint-disable-next-line @next/next/no-img-element -- a static icon, no optimisation needed */}
        <img src="/icons/icon-192.png" alt="" width={72} height={72} className="mx-auto rounded-2xl" />
        <h1 className="mt-5 text-2xl font-semibold tracking-tight">LLMBook for Android</h1>
        <p className="mt-2 text-sm text-muted">
          The same LLMBook as this website, as an app on your phone. It needs this server to be
          running; your documents and answers stay on the server.
        </p>
        <a
          href="/download/LLMBook.apk"
          download
          className="mt-6 inline-block rounded-full bg-accent px-6 py-2.5 text-sm font-medium text-accent-foreground hover:opacity-90"
        >
          Download for Android
        </a>
        <ol className="mt-8 space-y-3 text-left text-sm">
          {STEPS.map((step, i) => (
            <li key={i} className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold text-accent">
                {i + 1}
              </span>
              <span className="text-muted">{step}</span>
            </li>
          ))}
        </ol>
        <p className="mt-8 text-xs text-muted">
          On an iPhone, or prefer no install? Open this site in your browser and choose{" "}
          <b>Add to Home Screen</b>.
        </p>
        <Link href="/login" className="mt-4 inline-block text-sm text-accent hover:underline">
          ← Back to sign in
        </Link>
      </div>
    </main>
  );
}
