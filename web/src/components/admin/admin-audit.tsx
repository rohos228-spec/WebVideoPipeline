"use client";

/**
 * Вкладка «Аудит» админ-панели VIA: журнал действий администраторов.
 * Поиск серверный (action/target/admin/payload), с дебаунсом 300 мс.
 */
import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ScrollText } from "lucide-react";
import { adminApi } from "@/lib/admin-api";
import {
  Badge,
  Card,
  Empty,
  ErrorBox,
  Field,
  Skel,
  Spinner,
  TextInput,
  fmtDateTime,
  fmtNum,
  tableCls,
  tableWrap,
  tdCls,
  thCls,
} from "@/components/admin/admin-kit";

const LIMIT = 200;

/** JSON payload строкой, обрезанный до 400 символов. */
function payloadText(payload: Record<string, unknown> | null | undefined): string {
  try {
    const s = JSON.stringify(payload ?? {}, null, 2);
    return s.length > 400 ? s.slice(0, 400) + "\n…" : s;
  } catch {
    return "—";
  }
}

export function AdminAuditTab() {
  const [q, setQ] = useState("");
  const [qDebounced, setQDebounced] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setQDebounced(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["admin", "audit", LIMIT, qDebounced],
    queryFn: () => adminApi.audit(LIMIT, qDebounced),
    placeholderData: keepPreviousData,
  });

  const items = data?.items ?? [];

  return (
    <div className="flex flex-col gap-3">
      <Card
        title="Журнал действий"
        icon={<ScrollText className="h-4 w-4" />}
        right={
          <span className="flex items-center gap-2 text-[13px] text-zinc-500">
            {isFetching && !isLoading ? <Spinner className="h-3.5 w-3.5" /> : null}
            показано {fmtNum(items.length)} из {fmtNum(data?.total)}
          </span>
        }
      >
        <div className="max-w-[360px]">
          <Field label="Поиск" hint="по администратору, действию, объекту и payload">
            <TextInput
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="admin, action, target или текст payload"
            />
          </Field>
        </div>
      </Card>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skel key={i} h={34} />
          ))}
        </div>
      ) : error ? (
        <ErrorBox err={error} retry={() => void refetch()} />
      ) : items.length === 0 ? (
        <Empty
          text={
            qDebounced
              ? "По запросу ничего не найдено. Попробуй другой текст."
              : "Журнал пуст — действий администраторов пока не было."
          }
        />
      ) : (
        <div className={tableWrap}>
          <table className={tableCls}>
            <thead>
              <tr>
                <th className={thCls}>Время</th>
                <th className={thCls}>Кто</th>
                <th className={thCls}>Действие</th>
                <th className={thCls}>Объект</th>
                <th className={thCls}>Payload</th>
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <tr key={e.id}>
                  <td className={tdCls}>
                    <div className="whitespace-nowrap">{fmtDateTime(e.ts)}</div>
                  </td>
                  <td className={tdCls}>
                    <div className="max-w-[160px] truncate" title={e.admin}>
                      {e.admin || "—"}
                    </div>
                  </td>
                  <td className={tdCls}>
                    <Badge tone="info">{e.action || "—"}</Badge>
                  </td>
                  <td className={tdCls}>
                    <div className="max-w-[240px] truncate font-mono text-[12px] text-zinc-400" title={e.target}>
                      {e.target || "—"}
                    </div>
                  </td>
                  <td className={tdCls}>
                    <details className="max-w-[460px]">
                      <summary className="cursor-pointer select-none text-[12px] text-zinc-500 hover:text-zinc-300">
                        JSON
                      </summary>
                      <pre className="mt-1 max-h-[220px] overflow-auto whitespace-pre-wrap break-all rounded-[8px] bg-black/35 p-2 text-[11px] leading-relaxed text-zinc-400">
                        {payloadText(e.payload)}
                      </pre>
                    </details>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
