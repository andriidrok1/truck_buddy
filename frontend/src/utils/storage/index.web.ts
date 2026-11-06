// Web storage (Metro picks index.ts on native).
// Helpers never throw: reads return `fallback`, writes return `false`.
// Values supported: string | number | boolean | null, or a plain object/array (JSON-serialized on disk).
// Usage: import { storage } from "@/src/utils/storage"; await storage.getItem(key, fallback);
// No Keychain on web — secure* helpers reuse AsyncStorage (no expo-secure-store).
// Because of that, a key SecureStore rejects on native (":" "/" spaces) works here, so the web preview
// never shows the bug. Keep secure keys to [A-Za-z0-9._-] ("auth_token"), same as index.ts.

import AsyncStorage from "@react-native-async-storage/async-storage";

import { removeAppKeys, wipeGate } from "./pendingWipe";
import { AssertNoExtras, StorageBase, StorageItemValue } from "./storage-base";

// A "Delete my data" the last run could not finish is finished before the first read (pendingWipe.ts).
const removeSecure = (key: string) => AsyncStorage.removeItem(key);
// null once the gate has settled: then a call costs no extra tick and keeps its place in line.
const pending = () => wipeGate(removeSecure);

/** Every key the app writes, a failed one retried once; the keys still there come back (pendingWipe.ts). */
export const removeAllAppKeys = (): Promise<string[]> => removeAppKeys(removeSecure);

export class Storage extends StorageBase {
  // General KV — backed by AsyncStorage (its built-in web shim uses IndexedDB).
  async getItem<Fallback extends StorageItemValue>(
    key: string,
    fallback: Fallback,
  ): Promise<Fallback | null> {
    try {
      const gate = pending();
      if (gate) await gate;
      const raw = await AsyncStorage.getItem(key);
      return this.retrieve(raw, fallback);
    } catch (e) {
      this.warn("getItem", key, e);
      return fallback;
    }
  }

  async setItem<Value extends StorageItemValue>(
    key: string,
    value: Value,
  ): Promise<boolean> {
    try {
      const gate = pending();
      if (gate) await gate;
      await AsyncStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch (e) {
      this.warn("setItem", key, e);
      return false;
    }
  }

  async removeItem(key: string): Promise<boolean> {
    try {
      const gate = pending();
      if (gate) await gate;
      await AsyncStorage.removeItem(key);
      return true;
    } catch (e) {
      this.warn("removeItem", key, e);
      return false;
    }
  }

  // Browsers have no Keychain — secure* helpers fall through to AsyncStorage.
  async secureGet<Fallback extends StorageItemValue>(
    key: string,
    fallback: Fallback,
  ): Promise<Fallback | null> {
    return this.getItem(key, fallback);
  }

  async secureSet<Value extends StorageItemValue>(
    key: string,
    value: Value,
  ): Promise<boolean> {
    return this.setItem(key, value);
  }

  async secureRemove(key: string): Promise<boolean> {
    return this.removeItem(key);
  }
}

export const storage = new Storage();

// Compile-time guard: any new method must be declared in storage-base.ts first.
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- intentional compile-time-only assertion
type _NoExtras = AssertNoExtras<Exclude<keyof Storage, keyof StorageBase>>;
