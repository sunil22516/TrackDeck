import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TrackDeck: move a playlist, keep the right versions",
  description: "Upload a playlist export, review uncertain matches, download a clean file.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
