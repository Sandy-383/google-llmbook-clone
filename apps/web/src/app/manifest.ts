import type { MetadataRoute } from "next";

/** Makes LLMBook installable ("Add to Home screen"), and is the basis of the Android app. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "LLMBook",
    short_name: "LLMBook",
    description:
      "Ask questions about your documents. Answers come only from your sources, with citations.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#121214",
    theme_color: "#4f46e5",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/maskable-192.png", sizes: "192x192", type: "image/png", purpose: "maskable" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
