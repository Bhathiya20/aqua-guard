import { describe, expect, it } from "vitest";
import vectors from "../../../../tests/qr_vectors.json";
import { QrError, formatCode, parseQr } from "./qr";

type Case = { in: string; ok?: { mac: string; code: string; type: string }; err?: string };

describe("QR parser against the shared vectors (tests/qr_vectors.json)", () => {
  const cases = (vectors as { cases: Case[] }).cases;
  it("has the shared vectors", () => expect(cases.length).toBeGreaterThanOrEqual(20));
  cases.forEach((c, i) => {
    it(`case ${i}: ${JSON.stringify(c.in).slice(0, 60)} -> ${c.ok ? "ok" : c.err}`, () => {
      if (c.ok) {
        expect(parseQr(c.in)).toEqual(c.ok);
      } else {
        try { parseQr(c.in); throw new Error("expected a QrError"); } catch (e) {
          expect(e).toBeInstanceOf(QrError);
          expect((e as QrError).kind).toBe(c.err);
        }
      }
    });
  });
  it("formats a code for display", () => expect(formatCode("0123456789AB")).toBe("0123-4567-89AB"));
});
