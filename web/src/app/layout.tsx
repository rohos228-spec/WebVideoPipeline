import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { BrandScrub } from "@/components/brand-scrub";

export const metadata: Metadata = {
  title: "VIA - видеостудия",
  description: "Нейросетевая видеостудия VIA: план → сценарий → видео → озвучка → публикация",
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
    ],
    shortcut: "/icon.svg",
    apple: "/icon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ru" suppressHydrationWarning className="dark">
      <body className="antialiased font-sans">
        <Providers>{children}</Providers>
        <BrandScrub />
      </body>
    </html>
  );
}
