"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Coins, Loader2, Sparkles, Ticket } from "lucide-react";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { useBalance } from "@/hooks/use-identity";
import { errorMessageFromUnknown } from "@/lib/error-message";

interface CouponDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CouponDialog({ open, onOpenChange }: CouponDialogProps) {
  const qc = useQueryClient();
  const { data: balanceData } = useBalance();
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanCode = code.trim().toUpperCase();
    if (!cleanCode) return;

    setLoading(true);
    setError(null);

    try {
      const res = await api.redeemCoupon(cleanCode);
      toast.success(res.message || `Купон ${res.code} успешно активирован!`);
      // Обновляем баланс во всём интерфейсе
      qc.invalidateQueries({ queryKey: ["billing-balance"] });
      qc.invalidateQueries({ queryKey: ["balance"] });
      qc.invalidateQueries({ queryKey: ["me"] });
      setCode("");
      onOpenChange(false);
    } catch (err: unknown) {
      const msg = errorMessageFromUnknown(err) || "Не удалось активировать купон";
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md border-white/10 bg-zinc-950/95 text-white backdrop-blur-2xl">
        <DialogHeader className="gap-1">
          <div className="flex items-center gap-2 text-cyan-400">
            <Ticket className="h-5 w-5" />
            <DialogTitle className="text-base font-bold tracking-tight">Пополнение баланса</DialogTitle>
          </div>
          <DialogDescription className="text-xs text-zinc-400">
            Введите промокод или подарочный купон для мгновенного начисления кредитов.
          </DialogDescription>
        </DialogHeader>

        {/* Текущий баланс */}
        <div className="flex items-center justify-between rounded-xl border border-white/10 bg-white/[0.03] p-3.5">
          <span className="text-xs text-zinc-400">Текущий баланс</span>
          <div className="flex items-center gap-1.5 text-sm font-semibold text-emerald-300">
            <Coins className="h-4 w-4 text-emerald-400" />
            <span>
              {balanceData?.unlimited
                ? "∞ (безлимит)"
                : balanceData?.balance_credits ?? "0,00 кр"}
            </span>
          </div>
        </div>

        {/* Форма ввода купона */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-[11px] font-medium uppercase tracking-wider text-zinc-400">
              Код купона
            </label>
            <div className="relative">
              <Input
                value={code}
                onChange={(e) => {
                  setCode(e.target.value.toUpperCase());
                  setError(null);
                }}
                placeholder="STUDIO1"
                disabled={loading}
                autoFocus
                className="h-10 border-white/10 bg-black/50 font-mono text-sm tracking-wider uppercase placeholder:text-zinc-600 focus-visible:border-cyan-400"
              />
            </div>
            {error && <p className="text-xs font-medium text-rose-400 animate-in fade-in">{error}</p>}
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={loading}
              onClick={() => onOpenChange(false)}
              className="text-xs text-zinc-400 hover:text-white"
            >
              Отмена
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={loading || !code.trim()}
              className="gap-2 bg-gradient-to-r from-cyan-500 to-blue-600 text-xs font-semibold text-white shadow-lg shadow-cyan-500/20 hover:brightness-110"
            >
              {loading ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  <span>Проверка…</span>
                </>
              ) : (
                <>
                  <Sparkles className="h-3.5 w-3.5" />
                  <span>Активировать</span>
                </>
              )}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
