// The account this phone is signed in to (Apple or Google), as the server reported it at sign-in: the name, the
// email and the provider, kept only to show them (the profile screen, "Welcome back"). Nothing secret: the tokens
// live in the Keychain (src/auth.ts). Sign-out and "Delete my data" remove the key.
//
// Two things that live only in memory sit here too, because they belong to the same moment:
//   - "Welcome back": a returning driver sees it once after signing in (src/components/WelcomeBack.tsx). Not
//     stored: an app killed before "Open the map" simply opens on the map next time.
//   - the sign-in screen's notice after the server turned the token down ("You were signed out. Sign in again.").

import { useEffect, useState } from "react";

import { storage } from "../utils/storage";

export const ACCOUNT_KEY = "account_v1";

export type Provider = "apple" | "google";
export type Account = { name: string | null; email: string | null; provider: Provider };

let account: Account | null | undefined; // undefined = not read yet
let reading: Promise<Account | null> | null = null;
let welcome = false;
let bypassed = false; // "Open the map anyway": the driver is let in while the profile is still on its way
let notice: "signed_out" | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

const text = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v.trim() : null);

/** What `storage.getItem(ACCOUNT_KEY)` hands back, or null when it is not an account. */
export function parseAccount(raw: unknown): Account | null {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  if (o.provider !== "apple" && o.provider !== "google") return null;
  return { name: text(o.name), email: text(o.email), provider: o.provider };
}

export function loadAccount(): Promise<Account | null> {
  if (account !== undefined) return Promise.resolve(account);
  if (!reading) {
    reading = storage
      .getItem<Record<string, unknown> | null>(ACCOUNT_KEY, null)
      .then((raw) => {
        if (account === undefined) account = parseAccount(raw); // a sign-in during the read wins
        emit();
        return account as Account | null;
      })
      .catch(() => {
        if (account === undefined) account = null;
        return account as Account | null;
      });
  }
  return reading;
}

/** null until the key is read (or when nobody is signed in to an account). */
export const getAccount = (): Account | null => account ?? null;

export async function saveAccount(next: Account): Promise<void> {
  account = { name: text(next.name), email: text(next.email), provider: next.provider };
  emit();
  await storage.setItem(ACCOUNT_KEY, account);
}

/** Sign-out, "Delete my data", a token the server turned down: no account, no "Welcome back" waiting. */
export async function forgetAccount(): Promise<void> {
  account = null;
  reading = null;
  welcome = false;
  bypassed = false;
  emit();
  await storage.removeItem(ACCOUNT_KEY).catch(() => false);
}

/** The first word of a full name ("Mike Johnson" -> "Mike"): what the name step of onboarding starts with. */
export function givenName(name: string | null | undefined): string {
  return (name ?? "").trim().split(/\s+/)[0] ?? "";
}

/** The profile the name step of onboarding starts from: the first name Apple or Google gave at sign-in, unless the
 *  driver already has one. The driver can say or type another; nothing is saved until onboarding finishes. */
export function withAccountName<T extends { driver_name?: string | null }>(profile: T, accountName: string | null | undefined): T {
  const first = givenName(accountName).slice(0, 40);
  return first && !profile.driver_name?.trim() ? { ...profile, driver_name: first } : profile;
}

export const welcomeBackDue = () => welcome;
export function setWelcomeBack(on: boolean) {
  if (on) bypassed = false;
  if (welcome === on) return;
  welcome = on;
  emit();
}

/** "Open the map anyway" on Welcome back, after the profile pull failed: the map opens while the restore stays
 *  pending (nothing is pushed, src/data/profile.ts). In memory only: after a restart Welcome back tries again. */
export function bypassRestore() {
  bypassed = true;
  welcome = false;
  emit();
}
export const restoreBypassed = () => bypassed;

export const signInNotice = () => notice;
export function setSignInNotice(next: "signed_out" | null) {
  if (notice === next) return;
  notice = next;
  emit();
}

function useStore<T>(read: () => T): T {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force((n) => n + 1);
    listeners.add(l);
    loadAccount();
    return () => {
      listeners.delete(l);
    };
  }, []);
  return read();
}

export const useAccount = (): Account | null => useStore(getAccount);
export const useWelcomeBack = (): boolean => useStore(welcomeBackDue);
export const useSignInNotice = (): "signed_out" | null => useStore(signInNotice);
export const useRestoreBypassed = (): boolean => useStore(restoreBypassed);

/** Tests only. */
export function resetAccountForTests() {
  account = undefined;
  reading = null;
  welcome = false;
  bypassed = false;
  notice = null;
  listeners.clear();
}
