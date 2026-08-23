"use client";

/**
 * Кто смотрит и сколько у него денег — один источник на весь интерфейс.
 *
 * Баланс спрашивается не по таймеру, а по событиям: шаг списывает деньги в
 * момент завершения, и опрос раз в тридцать секунд означал бы, что человек
 * полминуты видит старую цифру ровно тогда, когда она ему важнее всего.
 * События конвейера уже ходят по шине (`use-bus`), и `credits_required`
 * инвалидирует баланс сам.
 */

import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  captureTokenFromUrl,
  fetchAuthStatus,
  fetchBalance,
  fetchMe,
  getToken,
  type AuthStatus,
  type Balance,
  type Me,
} from "@/lib/identity-api";

export function useAuthStatus() {
  return useQuery<AuthStatus>({
    queryKey: ["auth-status"],
    queryFn: fetchAuthStatus,
    // Режим установки не меняется на ходу: спрашивать его повторно незачем.
    staleTime: Infinity,
    retry: false,
  });
}

export function useMe() {
  const { data: status } = useAuthStatus();
  const qc = useQueryClient();

  // Возврат из биллинга приносит токен в адресной строке. Забираем его до
  // первого запроса и сразу чистим URL.
  useEffect(() => {
    if (captureTokenFromUrl()) {
      qc.invalidateQueries({ queryKey: ["me"] });
      qc.invalidateQueries({ queryKey: ["billing-balance"] });
    }
  }, [qc]);

  return useQuery<Me>({
    queryKey: ["me"],
    queryFn: fetchMe,
    // В режиме владельца ручка ответит и без токена, но спрашивать её до
    // того, как известен режим, незачем: получим лишний 401 в консоли.
    enabled: status !== undefined && (!status.sso || Boolean(getToken())),
    retry: false,
  });
}

export function useBalance() {
  const { data: status } = useAuthStatus();
  return useQuery<Balance>({
    queryKey: ["billing-balance"],
    queryFn: fetchBalance,
    enabled: Boolean(status?.sso) && Boolean(getToken()),
    retry: false,
  });
}

/**
 * Нужно ли показать приглашение войти.
 *
 * Три состояния, а не два: режим владельца — не «не вошёл», а «личностей
 * здесь нет», и форма входа ему не нужна.
 */
export function useNeedsLogin(): boolean {
  const { data: status } = useAuthStatus();
  const { isError } = useMe();
  if (!status?.sso) return false;
  return !getToken() || isError;
}
