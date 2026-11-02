"use client";
import {useCallback, useEffect, useRef, useState} from "react";
import {http} from "@/lib/api";

// Публикации как у заказчика: шаг «Публикация» выкладывает ролик сразу на все площадки,
// где его ещё нет (профиль MoreLogin), и пишет ссылки в проект. Здесь только состояние;
// запустить публикацию можно из чата «ИИ-агент» — карточкой с подтверждением.
interface PublicationItem {
  platform: string; title: string; published: boolean; post_url: string | null; account: string;
}
interface PublicationFacts {
  project_id: number; project_status: string; publish_enabled: boolean; sha256: string | null;
  caption: string; video: {artifact_uuid: string; url: string} | null; accounts_error: string;
  platforms: PublicationItem[]; pending: string[];
}

export function PublicationsSection({projectId}: {projectId: number | null}) {
  const project = projectId ?? 0;
  const [facts, setFacts] = useState<PublicationFacts | null>(null);
  const [error, setError] = useState("");
  const activeProject = useRef(project);
  activeProject.current = project;

  useEffect(() => { setFacts(null); setError(""); }, [project]);

  const load = useCallback(async () => {
    if (!project) return;
    try {
      const data = await http<PublicationFacts>(`/api/projects/${project}/publications`);
      if (activeProject.current !== project) return;
      setFacts(data);
      setError("");
    } catch {
      if (activeProject.current === project) setError("Список публикаций недоступен.");
    }
  }, [project]);

  useEffect(() => {
    if (!project) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      await load();
      if (!cancelled) timer = setTimeout(tick, 8000);
    };
    void tick();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [load, project]);

  if (!projectId) return <p className="mt-3 text-xs text-white/50">Выберите проект, чтобы увидеть публикации.</p>;
  const done = facts ? facts.platforms.filter((p) => p.published).length : 0;
  return (
    <section data-testid="publications-panel" className="mt-3 space-y-2 rounded border border-white/10 p-2 text-xs text-white/80">
      <h4>Публикации{facts ? ` · ${done} из ${facts.platforms.length}` : ""}</h4>
      {facts && !facts.publish_enabled && (
        <p role="status" className="text-amber-200">
          Автопубликация выключена в настройках сервера: шаг «Публикация» ничего не выложит.
        </p>
      )}
      {facts?.accounts_error && <p role="status" className="text-amber-200">{facts.accounts_error}</p>}
      {facts && !facts.video && <p className="text-white/50">Финального видео пока нет.</p>}
      {facts?.video && (
        <p className="text-white/50">
          <a className="underline" href={facts.video.url} target="_blank" rel="noreferrer">Готовый ролик</a>
          {" "}· подпись: {facts.caption.slice(0, 80) || "пусто"}
        </p>
      )}
      <ul className="space-y-1">
        {facts?.platforms.map((p) => (
          <li key={p.platform} className="rounded border border-white/10 px-1.5 py-1">
            <span className="font-medium">{p.title}</span>
            {" · "}
            {p.published ? <span className="text-emerald-300">опубликовано</span> : "не опубликовано"}
            {p.account && <span className="text-white/50"> · {p.account}</span>}
            {p.post_url && (
              <div><a className="underline" href={p.post_url} target="_blank" rel="noreferrer">{p.post_url}</a></div>
            )}
          </li>
        ))}
      </ul>
      {facts && facts.pending.length > 0 && facts.video && (
        <p className="text-white/50">
          Опубликовать можно из чата «ИИ-агент»: попросите «опубликуй ролик», агент покажет карточку
          со всеми площадками без публикации, и ролик уйдёт только после вашего подтверждения.
        </p>
      )}
      {error && <p role="alert" className="text-amber-200">{error}</p>}
    </section>
  );
}
