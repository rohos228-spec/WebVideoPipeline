const LINKS = [
  { href: "/legal/offer", label: "Публичная оферта" },
  { href: "/legal/privacy", label: "Политика обработки персональных данных" },
] as const;

export function LegalFooter() {
  return (
    <footer className="border-t border-white/[0.06] px-6 py-4 text-center text-[12px] leading-relaxed text-white/30">
      <p>© 2026 VIA · ИП Крохмаль М. В. · ИНН ____________ · ОГРНИП _______________</p>
      <nav className="mt-1.5 flex flex-wrap items-center justify-center gap-x-4 gap-y-1">
        {LINKS.map((link) => (
          <a key={link.href} href={link.href} className="hover:text-white/50">
            {link.label}
          </a>
        ))}
      </nav>
    </footer>
  );
}
