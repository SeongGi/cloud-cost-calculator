import type { Metadata, Viewport } from "next";
import Script from "next/script";
import "./globals.css";
import "./chat.css";
import "@copilotkit/react-core/v2/styles.css";
import { Providers } from "./providers";
import { THEME_BOOT_SCRIPT } from "@/hooks/use-theme";
import { SITE } from "@/lib/site";

export const metadata: Metadata = {
  metadataBase: new URL(SITE.url),
  title: SITE.title,
  description: SITE.description,
  keywords: SITE.keywords,
  applicationName: SITE.name,
  alternates: { canonical: "/" },
  icons: { icon: "/icon.svg" },
  authors: [{ name: SITE.author, url: SITE.about }],
  creator: SITE.author,
  robots: { index: true, follow: true },
  // Search Console ownership check (public value by design).
  verification: { google: "aAd1tMkUmArDEnsIWQp-hWzoA3nly9Asn88eRj-Tw0M" },
  openGraph: {
    type: "website", url: SITE.url, siteName: SITE.name, title: SITE.title, description: SITE.description, locale: "ko_KR",
    images: [{ url: "/og.png", width: 1200, height: 630, alt: "클라우드 비용 계산기 화면" }],
  },
  twitter: { card: "summary_large_image", title: SITE.title, description: SITE.description, images: ["/og.png"] },
};

// On Android the keyboard shrinks the page instead of covering it, so the input stays above it.
export const viewport: Viewport = { width: "device-width", initialScale: 1, interactiveWidget: "resizes-content" };

// Structured data so search engines know this is a free web app and who made it.
const JSON_LD = {
  "@context": "https://schema.org",
  "@type": "WebApplication",
  name: SITE.name,
  url: SITE.url,
  description: SITE.description,
  applicationCategory: "FinanceApplication",
  operatingSystem: "Web",
  inLanguage: "ko",
  isAccessibleForFree: true,
  offers: { "@type": "Offer", price: "0", priceCurrency: "KRW" },
  author: { "@type": "Person", name: SITE.author, url: SITE.about, sameAs: [SITE.blog, SITE.about] },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <body className="antialiased">
        <Script id="theme-boot" strategy="beforeInteractive">{THEME_BOOT_SCRIPT}</Script>
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(JSON_LD) }} />
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
