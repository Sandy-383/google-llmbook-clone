"use client";

import { useEffect } from "react";

/** Registers public/sw.js, which shows an offline page when the server can't be reached. */
export function ServiceWorker() {
  useEffect(() => {
    if ("serviceWorker" in navigator && process.env.NODE_ENV === "production") {
      navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" }).catch(() => {
        // Not fatal: the app works the same without it, just without the offline page.
      });
    }
  }, []);
  return null;
}
