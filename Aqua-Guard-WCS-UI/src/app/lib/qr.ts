// Aqua Guard node QR / pairing-code parser. TypeScript twin of firmware/gateway/portal/qr.js, aqua_join.h and tools/aqua_qr.py.
// Shared test vectors: tests/qr_vectors.json (checked in src/app/lib/qr.test.ts).
const ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";   // Crockford base32: no I, L, O, U
const CODE_LEN = 12;

export type QrErrorKind = "format" | "version" | "mac" | "code" | "type";
export class QrError extends Error {
  kind: QrErrorKind;
  constructor(kind: QrErrorKind, msg = "") { super(kind + (msg ? ": " + msg : "")); this.kind = kind; }
}
export interface ParsedQr { mac: string; code: string; type: "flow" | "rain" }

export function normalizeCode(text: string): string {
  let out = "";
  for (const raw of text) {
    if (raw === "-" || raw === " " || raw === "_") continue;
    let ch = raw.toUpperCase();
    if (ch === "O") ch = "0"; else if (ch === "I" || ch === "L") ch = "1";
    if (!ALPHABET.includes(ch)) throw new QrError("code", "invalid symbol");
    out += ch;
  }
  if (out.length !== CODE_LEN) throw new QrError("code", "expected 12 symbols");
  return out;
}
export const formatCode = (c: string) => `${c.slice(0, 4)}-${c.slice(4, 8)}-${c.slice(8, 12)}`;

export function normalizeMac(text: string): string {
  const h = text.replace(/[:\- ]/g, "").toUpperCase();
  if (!/^[0-9A-F]{12}$/.test(h)) throw new QrError("mac", "bad MAC");
  return h.match(/../g)!.join(":");
}
const safeDecode = (s: string) => { try { return decodeURIComponent(s.replace(/\+/g, " ")); } catch { return s; } };

/** Error precedence matches the firmware: format -> version -> mac -> code -> type. */
export function parseQr(text: string): ParsedQr {
  const t = (text || "").trim();
  let mac: string, code: string, typ: string;
  if (!t || t.length >= 200) throw new QrError("format");
  if (t.slice(0, 3).toUpperCase() === "AQG") {
    const f = t.split(";");
    if (f.length !== 4) throw new QrError("format", "compact form needs 4 fields");
    if (f[0].toUpperCase() !== "AQG1") throw new QrError("version");
    [, mac, code, typ] = f;
  } else {
    const qi = t.indexOf("?");
    if (qi < 0 || t.indexOf("/add") < 0) throw new QrError("format");
    const q: Record<string, string> = {};
    for (const part of t.slice(qi + 1).split("#")[0].split("&")) {
      const kv = part.split("=");
      const k = safeDecode(kv[0]);
      if (!(k in q)) q[k] = safeDecode(kv.slice(1).join("="));
    }
    if (!("v" in q) || !("mac" in q) || !("code" in q) || !("type" in q)) throw new QrError("format", "missing field");
    if (q.v !== "1") throw new QrError("version");
    mac = q.mac; code = q.code; typ = q.type;
  }
  const nm = normalizeMac(mac), nc = normalizeCode(code), ty = typ.toLowerCase();
  if (ty !== "flow" && ty !== "rain") throw new QrError("type");
  return { mac: nm, code: nc, type: ty };
}

export const QR_ERROR_TEXT: Record<QrErrorKind, string> = {
  format: "This is not an Aqua Guard node QR code.",
  version: "This QR code is from a different version. Update the app or use the gateway page.",
  mac: "The MAC address in the code is not valid.",
  code: "The pairing code in the code is not valid.",
  type: "The node type in the code is not valid.",
};
