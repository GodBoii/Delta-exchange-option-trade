import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { ThemeProvider, THEME_BOOT_SCRIPT } from "@/app/components/theme";
import PwaRuntime from "@/app/components/PwaRuntime";
import { RealtimeSignalsProvider } from "@/app/components/RealtimeSignals";
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
  icons: {
    icon: [{ url: "/icon.png", type: "image/png", sizes: "256x256" }],
    shortcut: "/icon.png",
    /* iOS composites a transparent home-screen icon onto black, so the Apple
       icon is the one variant with its own opaque backdrop. */
    apple: [{ url: "/icons/apple-touch-icon.png", type: "image/png", sizes: "180x180" }]
  },
  /**
   * iOS reads the manifest for the icon and the name but not for `display`, so
   * running full screen from the home screen still needs Apple's own meta tag.
   * `default` keeps the status bar as its own opaque strip: the alternative,
   * `black-translucent`, would run the page under the clock, and the shell's
   * sticky header has no top safe-area inset to survive that.
   */
  appleWebApp: {
    capable: true,
    title: "Trade Cognition",
    statusBarStyle: "default"
  },
  /* Next emits only the standard `mobile-web-app-capable` for `capable` above.
     iOS before 17.4 reads the Apple spelling, which has no typed field, so it
     is added by hand to keep older iPhones opening full screen. */
  other: { "apple-mobile-web-app-capable": "yes" },
  robots: { index: false, follow: false }
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#080809" },
    { media: "(prefers-color-scheme: light)", color: "#f4f2ee" }
  ],
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    /* `data-theme` is written by the boot script below before first paint, so
       the server markup deliberately carries no theme and React is told not to
       warn about the difference. */
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body>
        <ThemeProvider>
          <RealtimeSignalsProvider>{children}</RealtimeSignalsProvider>
          {/* Registers the service worker and owns the home-screen install
              offer. Mounted here rather than in a page because Chrome fires
              `beforeinstallprompt` once per load, and a listener attached after
              that moment never hears it. */}
          <PwaRuntime />
        </ThemeProvider>
      </body>
    </html>
  );
}
