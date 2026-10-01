"use client";

import { useEffect, useState } from "react";

export type DevRole = "admin" | "member";

const DEV_ROLE_KEY = "studio.dev_preview_role";

export function useDevRole() {
  const [devRole, setDevRoleState] = useState<DevRole>("admin");

  useEffect(() => {
    if (typeof window === "undefined") return;
    const saved = window.localStorage.getItem(DEV_ROLE_KEY);
    if (saved === "member" || saved === "admin") {
      setDevRoleState(saved);
    }

    const handler = (e: Event) => {
      const custom = e as CustomEvent<DevRole>;
      if (custom.detail === "member" || custom.detail === "admin") {
        setDevRoleState(custom.detail);
      }
    };
    window.addEventListener("studio-dev-role-changed", handler);
    return () => window.removeEventListener("studio-dev-role-changed", handler);
  }, []);

  const setDevRole = (role: DevRole) => {
    setDevRoleState(role);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(DEV_ROLE_KEY, role);
      window.dispatchEvent(new CustomEvent("studio-dev-role-changed", { detail: role }));
    }
  };

  const toggleDevRole = () => {
    setDevRole(devRole === "admin" ? "member" : "admin");
  };

  return {
    devRole,
    setDevRole,
    toggleDevRole,
    isMemberPreview: devRole === "member",
  };
}
