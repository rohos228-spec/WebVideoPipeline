"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { subscribeWS } from "@/lib/api";
import {
  listChatNotices,
  markChatNoticeRead,
  markNoticeReadLocal,
  mergeNotices,
  noticeInScope,
  readNotice,
  type ChatNotice,
} from "@/lib/chat-notices";

const CATCH_UP_MS = 60_000;

export function useChatNotices(projectId: number | null) {
  const [notices, setNotices] = useState<ChatNotice[]>([]);
  const [loadError, setLoadError] = useState("");
  const lastId = useRef(0);

  const absorb = useCallback(
    (items: ChatNotice[]) => {
      const scoped = items.filter((n) => noticeInScope(n, projectId));
      for (const n of scoped) if (n.id > lastId.current) lastId.current = n.id;
      setNotices((cur) => mergeNotices(cur, scoped));
    },
    [projectId],
  );

  useEffect(() => {
    lastId.current = 0;
    setNotices([]);
    setLoadError("");
    let alive = true;

    const catchUp = async () => {
      try {
        const res = await listChatNotices(projectId, lastId.current);
        if (!alive) return;
        absorb((res.notices ?? []).flatMap((raw) => {
          const n = readNotice(raw);
          return n ? [n] : [];
        }));
        setLoadError("");
      } catch {
        if (alive) setLoadError("Уведомления сейчас не загрузились. Они появятся, когда сервер снова ответит.");
      }
    };

    void catchUp();
    const onWs = (raw: unknown) => {
      if (!raw || typeof raw !== "object") return;
      const evt = raw as { type?: unknown; notice?: unknown; notice_id?: unknown };
      if (evt.type === "chat_notice") {
        const n = readNotice(evt.notice);
        if (n) absorb([n]);
      } else if (evt.type === "chat_notice_read") {
        const id = Number(evt.notice_id);
        if (Number.isFinite(id)) setNotices((cur) => markNoticeReadLocal(cur, id));
      }
    };
    const unsubs = [subscribeWS("global", onWs)];
    if (projectId != null) unsubs.push(subscribeWS(`projects.${projectId}`, onWs));
    const timer = window.setInterval(() => void catchUp(), CATCH_UP_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void catchUp();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      for (const off of unsubs) off();
    };
  }, [absorb, projectId]);

  const markRead = useCallback(async (id: number) => {
    setNotices((cur) => markNoticeReadLocal(cur, id));
    try {
      await markChatNoticeRead(id);
    } catch {
      return;
    }
  }, []);

  const unread = notices.filter((n) => !n.read).length;

  return { notices, unread, loadError, markRead };
}
