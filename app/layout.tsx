import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Clip Create Studio",
  description: "Compose YouTube-ready movies from fifteen second clips.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className="h-full antialiased"
    >
      <body className="min-h-full bg-[var(--bg)] text-[var(--ink)]">
        {children}
      </body>
    </html>
  );
}
