import type { Metadata, Viewport } from "next";
import { Providers } from "./providers";
import { ServiceWorker } from "./service-worker";
import "./globals.css";

export const metadata: Metadata = {
  title: "LLMBook",
  description: "Ask questions about your documents. Runs entirely on your machine.",
  applicationName: "LLMBook",
  appleWebApp: { capable: true, title: "LLMBook", statusBarStyle: "black-translucent" },
  icons: { apple: "/icons/apple-touch-icon.png" },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f7f8" },
    { media: "(prefers-color-scheme: dark)", color: "#121214" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="flex min-h-full flex-col font-sans">
        <Providers>{children}</Providers>
        <ServiceWorker />
      </body>
    </html>
  );
}
