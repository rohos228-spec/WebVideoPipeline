import type { Metadata } from "next";

import { AdminPanel } from "@/components/admin/admin-shell";

export const metadata: Metadata = {
  title: "VIA — Админ-панель",
  description: "Метрики, пользователи, генерации, запросы, начисления и подписки",
};

export default function AdminPage() {
  return <AdminPanel />;
}
