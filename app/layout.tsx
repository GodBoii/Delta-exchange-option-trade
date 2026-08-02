import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Delta Strategy Desk",
  description: "Build, preview, schedule, and monitor Delta Exchange option strategies."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
