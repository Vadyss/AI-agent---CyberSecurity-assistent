// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Frankenstein",
  description: "Build, test, and review detection rules.",
  authors: [{ name: "Adam Krúpa, Ondra Csajka" }],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en-US">
      <body>
        {children}
        <footer className="workspace-footer">© 2026 Adam Krúpa &amp; Ondra Csajka. MIT License.</footer>
      </body>
    </html>
  );
}
