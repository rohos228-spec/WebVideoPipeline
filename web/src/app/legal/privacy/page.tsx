import type { Metadata } from "next";
import { LegalFooter } from "@/components/shell/legal-footer";

export const metadata: Metadata = { title: "Политика обработки персональных данных — VIA" };

export default function PrivacyPage() {
  return (
    <div className="flex min-h-screen flex-col bg-[#070909] text-zinc-200">
      <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col px-6 py-16">
        <a href="/home" className="mb-8 text-sm text-white/40 hover:text-white/70">
          ← На главную
        </a>
        <h1 className="text-3xl font-semibold text-white">Политика обработки персональных данных</h1>
        <p className="mt-6 text-lg text-white/55">Документ готовится</p>
      </main>
      <LegalFooter />
    </div>
  );
}
