"use client";

import { useEffect, useState } from "react";

/** Keep JS-only drawers on the same breakpoint as Tailwind's md layout. */
export function useMobileLayout() {
  const [mobile, setMobile] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px)");
    const update = () => setMobile(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return mobile;
}
