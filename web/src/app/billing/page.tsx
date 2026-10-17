import { BillingPage } from "@/components/billing/billing-page";
import { AuthGate } from "@/components/auth/auth-gate";

export default function Page() {
  return (
    <AuthGate>
      <BillingPage />
    </AuthGate>
  );
}
