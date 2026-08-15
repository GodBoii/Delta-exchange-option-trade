import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

/**
 * Fonts are self-hosted by next/font. The previous stylesheet imported Geist
 * from fonts.googleapis.com, which the app's own Content-Security-Policy
 * (`style-src 'self'`) blocked, so the interface silently fell back to a system
 * face. Serving from the app origin fixes the policy violation and removes the
 * render-blocking third-party request.
 */
const sans = Geist({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap"
});

const mono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap"
});

export const metadata: Metadata = {
  title: {
    default: "Trade Cognition",
    template: "%s · Trade Cognition"
  },
  description: "Configure, schedule, and review Delta Exchange India option strategies, with live market and news analysis.",
  applicationName: "Trade Cognition",
  robots: { index: false, follow: false }
};

export const viewport: Viewport = {
  themeColor: "#08090b",
  colorScheme: "dark"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
