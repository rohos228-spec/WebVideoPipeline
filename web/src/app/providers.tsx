"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { useState } from "react";

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 3_000, refetchOnWindowFocus: false, retry: 1 },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      {children}
      <Toaster
        position="bottom-center"
        toastOptions={{
          style: {
            background: "var(--surface-overlay)",
            color: "var(--content)",
            border: "1px solid var(--border-strong)",
            borderRadius: "var(--radius-md)",
            fontFamily: "var(--font-body)",
          },
        }}
      />
    </QueryClientProvider>
  );
}
