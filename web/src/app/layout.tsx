import type { Metadata } from "next";
import { Manrope, Literata, JetBrains_Mono, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";
import { AuthGate } from "@/components/auth-gate";

const manrope = Manrope({
  variable: "--font-manrope",
  subsets: ["latin", "cyrillic"],
  display: "swap",
});

// Засечный шрифт с кириллицей — заголовки и сам текст ролика читаются как
// рукопись, а не как интерфейс. Ради этого и весь светлый лист.
const literata = Literata({
  variable: "--font-literata",
  subsets: ["latin", "cyrillic"],
  weight: ["400", "500", "600"],
  display: "swap",
});

const mono = JetBrains_Mono({
  variable: "--font-mono-src",
  subsets: ["latin", "cyrillic"],
  weight: ["400", "500"],
  display: "swap",
});

// Моно конструктора (Canon C): в скоупе конструктора --typeface-mono
// переключается на него, лист остаётся на JetBrains Mono.
const ibmPlexMono = IBM_Plex_Mono({
  variable: "--font-ibm-plex-mono",
  subsets: ["latin", "cyrillic"],
  weight: ["400", "500"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Видеостудия",
  description: "Идея → сценарий → кадры → видео. Каждый шаг с ценой.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // Переменные шрифтов вешаются на <html>: токены в globals.css считаются
  // на :root, и на <body> они бы до них не дошли.
  return (
    <html lang="ru" className={`${manrope.variable} ${literata.variable} ${mono.variable} ${ibmPlexMono.variable}`}>
      <body>
        {/* Дверь стоит ВНУТРИ провайдеров: экран входа сам ходит по сети
            и показывает состояние загрузки теми же средствами. */}
        <Providers>
          <AuthGate>{children}</AuthGate>
        </Providers>
      </body>
    </html>
  );
}
