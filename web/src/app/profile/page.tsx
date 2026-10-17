import { ProfilePage } from "@/components/panels/top/profile-page";
import { AuthGate } from "@/components/auth/auth-gate";

export const metadata = { title: "Профиль · VIA" };

export default function Page() {
  return (
    <AuthGate>
      <ProfilePage />
    </AuthGate>
  );
}
