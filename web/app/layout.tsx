import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Flexy — Linux package preparation",
  description: "Inspect supported Debian packages and prepare reviewed Arch Linux package conversions remotely.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
