export function InDevelopment() {
  const items = [
    { title: "Выплаты", text: "Вывод средств и партнёрские выплаты" },
    { title: "Хранилище", text: "Облачное хранилище проектов и медиа" },
  ];
  return (
    <section className="grid gap-3 sm:grid-cols-2" data-testid="in-development">
      {items.map((i) => (
        <div key={i.title} aria-disabled className="rounded-xl border border-dashed border-zinc-800 p-4 opacity-70">
          <div className="flex items-center justify-between">
            <h4 className="font-semibold text-zinc-200">{i.title}</h4>
            <span className="rounded bg-zinc-800 px-2 py-0.5 text-xs text-zinc-400">в разработке</span>
          </div>
          <p className="mt-1 text-xs text-zinc-500">{i.text}</p>
        </div>
      ))}
    </section>
  );
}
