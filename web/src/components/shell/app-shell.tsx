"use client";

import { Sparkles } from "lucide-react";
import { Topbar } from "./topbar";
import { LoginGate } from "./login-gate";
import { useNeedsLogin } from "@/hooks/use-identity";

export function AppShell({ children }: { children: React.ReactNode }) {
  // Без токена в режиме SaaS не отвечает ни одна ручка `/api/*`. Показать
  // канвас под баннером «войдите» значит предложить человеку нажимать на то,
  // что молча не работает, — поэтому вход закрывает интерфейс целиком.
  // В режиме владельца хук всегда возвращает false: там личностей нет.
  if (useNeedsLogin()) return <LoginGate />;

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
      {/* Topbar держит UiContext + модалки; children — sidebar/canvas/inspector */}
      <Topbar>{children}</Topbar>
    </div>
  );
}

export { Sparkles };
