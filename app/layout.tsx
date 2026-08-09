import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Trade Cognition",
  description: "Configure Delta Exchange strategies and analyze live market structure."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
