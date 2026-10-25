import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";
import "./chat.css";
import "@copilotkit/react-core/v2/styles.css";
import { Providers } from "./providers";
import { THEME_BOOT_SCRIPT } from "@/hooks/use-theme";

export const metadata: Metadata = {
  title: "클라우드 비용 계산기",
  description: "AWS, Azure, GCP 구성을 말로 설명하면 공개 가격표로 월 비용을 계산합니다.",
  icons: { icon: "/icon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <body className="antialiased">
        <Script id="theme-boot" strategy="beforeInteractive">{THEME_BOOT_SCRIPT}</Script>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
