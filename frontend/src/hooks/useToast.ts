import { useCallback, useEffect, useState } from "react";

import type { ToastData } from "../components/Toast";

/** The map screen's toast: shown by showToast, gone after 3.2 s (moved verbatim from app/index.tsx). */
export function useToast() {
  const [toast, setToast] = useState<ToastData | null>(null);
  const showToast = useCallback((text: string, kind: ToastData["kind"] = "error") => {
    setToast({ id: Date.now(), text, kind });
  }, []);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3200);
    return () => clearTimeout(t);
  }, [toast]);
  return { toast, showToast };
}
