import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  // Self-contained server bundle for the Docker image.
  output: "standalone",
  poweredByHeader: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  experimental: {
    // The dev proxy below only forwards the first 10 MB of a body by default; uploads go to 50 MB.
    proxyClientMaxBodySize: "60mb",
  },
  async headers() {
    return [
      {
        // The service worker must never be served from a cache, or fixes would never arrive.
        source: "/sw.js",
        headers: [
          { key: "Content-Type", value: "application/javascript; charset=utf-8" },
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
          { key: "Content-Security-Policy", value: "default-src 'self'; script-src 'self'" },
        ],
      },
    ];
  },
  // In `next dev`, forward /api to the local FastAPI server so the browser sees one origin.
  // In Docker, Caddy does this routing instead, so production builds get no rewrites.
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    const api = process.env.API_DEV_URL ?? "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
};

export default nextConfig;
