// Reading our own tokens on the phone: when one expires and whose it is. No signature check: the server does that.

const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/** atob without relying on the engine having it (the payload is ASCII JSON). */
function decodeBase64(s: string): string {
  let out = "";
  let bits = 0;
  let acc = 0;
  for (const ch of s) {
    if (ch === "=") break;
    const v = B64.indexOf(ch);
    if (v < 0) throw new Error("not base64");
    acc = (acc << 6) | v;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out += String.fromCharCode((acc >> bits) & 0xff);
    }
  }
  return out;
}

function claims(t: string | null | undefined): Record<string, unknown> | null {
  const part = t?.split(".")[1];
  if (!part) return null;
  try {
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (part.length % 4)) % 4);
    const v = JSON.parse(decodeBase64(b64)) as unknown;
    return v !== null && typeof v === "object" ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** The `exp` of a JWT in ms, or null when the token is not one we can read. */
export function tokenExpiry(t: string | null): number | null {
  const exp = claims(t)?.exp;
  return typeof exp === "number" && Number.isFinite(exp) ? exp * 1000 : null;
}

/** The `sub` of a JWT: the id the driver's data is kept under on the server (the same on every phone of an
 *  account). null when the token is not one we can read. */
export function tokenSub(t: string | null | undefined): string | null {
  const sub = claims(t)?.sub;
  return typeof sub === "string" && sub ? sub : null;
}
