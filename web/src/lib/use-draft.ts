"use client";

import { useEffect, useRef, useState } from "react";

export type SaveState = "idle" | "dirty" | "saving" | "saved" | "error";

/**
 * Черновик поля: правка живёт локально, на сервер уезжает через паузу.
 *
 * Отдельный хук, потому что правка текста — основное занятие в этом
 * интерфейсе, и «сохранить» кнопкой означало бы кнопку у каждого абзаца.
 * Пока идёт правка, входящее значение с сервера игнорируется — иначе
 * ответ на предыдущий PATCH затирает то, что человек уже дописал.
 */
export function useDraft<T>(
  serverValue: T,
  save: (value: T) => Promise<unknown>,
  delay = 900,
): [T, (value: T) => void, SaveState] {
  const [value, setValue] = useState<T>(serverValue);
  const [state, setState] = useState<SaveState>("idle");
  const dirty = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveRef = useRef(save);
  saveRef.current = save;

  useEffect(() => {
    if (!dirty.current) setValue(serverValue);
  }, [serverValue]);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const update = (next: T) => {
    setValue(next);
    dirty.current = true;
    setState("dirty");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      setState("saving");
      try {
        await saveRef.current(next);
        dirty.current = false;
        setState("saved");
        setTimeout(() => setState((s) => (s === "saved" ? "idle" : s)), 1_600);
      } catch {
        setState("error");
      }
    }, delay);
  };

  return [value, update, state];
}

export function saveHint(state: SaveState): string {
  switch (state) {
    case "dirty":
      return "не сохранено";
    case "saving":
      return "сохраняю…";
    case "saved":
      return "сохранено";
    case "error":
      return "не сохранилось";
    default:
      return "";
  }
}
