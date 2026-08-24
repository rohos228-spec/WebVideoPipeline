import type { Metadata } from "next";
import { Manrope, Literata, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";

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

export const metadata: Metadata = {
  title: "Видеостудия",
  description: "Идея → сценарий → кадры → видео. Каждый шаг с ценой.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // Переменные шрифтов вешаются на <html>: токены в globals.css считаются
  // на :root, и на <body> они бы до них не дошли.
  return (
    <html lang="ru" className={`${manrope.variable} ${literata.variable} ${mono.variable}`}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
