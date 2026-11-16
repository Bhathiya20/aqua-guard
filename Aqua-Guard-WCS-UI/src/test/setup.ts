import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => cleanup());

// jsdom lacks these; React Flow, Radix and recharts expect them.
class RO { observe() {} unobserve() {} disconnect() {} }
(globalThis as any).ResizeObserver ??= RO;
(globalThis as any).DOMMatrixReadOnly ??= class { m22 = 1; constructor(_?: string) {} };
if (!window.matchMedia) {
  (window as any).matchMedia = (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false });
}
Element.prototype.scrollIntoView ??= () => {};
(Element.prototype as any).hasPointerCapture ??= () => false;
(Element.prototype as any).releasePointerCapture ??= () => {};
