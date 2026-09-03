"use client";

/**
 * Выбор разрешения перед рендером — вместе с ценой, а не вместо неё.
 *
 * Решение владельца: разрешение выбирается на шаге генерации, а не задаётся
 * один раз на проект. Значит это выбор ЦЕНЫ: 720p и 1080P отличаются вдвое
 * (13.68 против 23.76 кредита за двадцать четыре клипа). Показать два слова
 * без цифр — предложить решение вслепую, а узнает человек об этом из счёта.
 *
 * Поэтому цена стоит на кнопке, а не в подсказке под ней: подсказку читают
 * те, кто уже сомневается, а увидеть разницу должны все.
 *
 * Выбор доезжает до кассы вместе с запуском (`runStep` с `resolution`), а не
 * сохраняется отдельным действием: сохранённый заранее, он разъехался бы с
 * ценой, которую человек только что видел.
 */

import * as React from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { authHeaders } from "@/lib/identity-api";
import { cn } from "@/lib/utils";

interface Option {
  id: string;
  label: string;
  price_credits: string;
  price_micro: number;
  exact: boolean;
  current: boolean;
  note: string;
}

export function VideoResolutionPicker({
  projectId,
  value,
  onChange,
  className,
}: {
  projectId: number;
  value: string;
  onChange: (id: string) => void;
  className?: string;
}) {
  const [options, setOptions] = React.useState<Option[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const res = await fetch(`/api/projects/${projectId}/steps/video/options`, {
          headers: authHeaders(),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as Option[];
        if (!alive) return;
        setOptions(data);
        // Предвыбор — то, что уже стоит у проекта; иначе первый вариант.
        if (!value) onChange(data.find((o) => o.current)?.id ?? data[0]?.id ?? "");
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  if (error) {
    return (
      <p className={cn("text-sm text-muted-foreground", className)}>
        Цены разрешений не загрузились: {error}
      </p>
    );
  }
  if (!options) {
    return (
      <span className={cn("flex items-center gap-2 text-sm text-muted-foreground", className)}>
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        считаю цены…
      </span>
    );
  }

  return (
    <div className={cn("space-y-2", className)}>
      <p className="text-sm text-muted-foreground">Разрешение — это и цена:</p>
      <div className="flex flex-wrap gap-2">
        {options.map((o) => (
          <Button
            key={o.id}
            size="sm"
            variant={value === o.id ? "default" : "outline"}
            onClick={() => onChange(o.id)}
            title={o.note || undefined}
            className="gap-2"
          >
            <span>{o.label}</span>
            <span className="tabular-nums opacity-80">
              {o.price_credits}
              {o.exact ? "" : "~"} кр
            </span>
          </Button>
        ))}
      </div>
    </div>
  );
}
