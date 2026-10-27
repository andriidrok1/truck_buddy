import { useEffect, useState } from "react";

import { storage } from "./utils/storage";

// Apple 5.1.2(i): the driver must agree before voice and location go to third-party AI and map services.
const KEY = "ai_consent_v1";
let state: "loading" | "yes" | "no" = "loading";
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

async function load() {
  if (state !== "loading") return;
  state = (await storage.getItem(KEY, false as boolean)) === true ? "yes" : "no";
  emit();
}

export async function giveConsent() {
  await storage.setItem(KEY, true);
  state = "yes";
  emit();
}

export function useConsent() {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force((n) => n + 1);
    listeners.add(l);
    load();
    return () => {
      listeners.delete(l);
    };
  }, []);
  return state;
}
