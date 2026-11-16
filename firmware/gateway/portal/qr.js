/* Aqua Guard node QR / pairing-code parser - JS twin of aqua_join.h (firmware) and tools/aqua_qr.py.
 * Shared test cases: tests/qr_vectors.json (run by scripts/test_portal_qr.mjs).  Works in the browser and in Node. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.AquaQr = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";            // Crockford base32: no I, L, O, U
  var CODE_LEN = 12;

  function QrError(kind, msg) { var e = new Error(kind + (msg ? ": " + msg : "")); e.kind = kind; return e; }

  function normalizeCode(text) {
    var out = "";
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (ch === "-" || ch === " " || ch === "_") continue;
      ch = ch.toUpperCase();
      if (ch === "O") ch = "0"; else if (ch === "I" || ch === "L") ch = "1";
      if (ALPHABET.indexOf(ch) < 0) throw QrError("code", "invalid symbol");
      out += ch;
    }
    if (out.length !== CODE_LEN) throw QrError("code", "expected 12 symbols");
    return out;
  }
  function formatCode(c) { return c.slice(0, 4) + "-" + c.slice(4, 8) + "-" + c.slice(8, 12); }
  function normalizeMac(text) {
    var h = text.replace(/[:\- ]/g, "").toUpperCase();
    if (!/^[0-9A-F]{12}$/.test(h)) throw QrError("mac", "bad MAC");
    return h.match(/../g).join(":");
  }
  function safeDecode(s) { try { return decodeURIComponent(s.replace(/\+/g, " ")); } catch (e) { return s; } }

  // Error precedence matches the firmware: format -> version -> mac -> code -> type
  function parse(text) {
    var t = (text || "").trim(), mac, code, typ;
    if (!t || t.length >= 200) throw QrError("format");
    if (t.slice(0, 3).toUpperCase() === "AQG") {
      var f = t.split(";");
      if (f.length !== 4) throw QrError("format", "compact form needs 4 fields");
      if (f[0].toUpperCase() !== "AQG1") throw QrError("version");
      mac = f[1]; code = f[2]; typ = f[3];
    } else {
      var qi = t.indexOf("?");
      if (qi < 0 || t.indexOf("/add") < 0) throw QrError("format");
      var q = {}, parts = t.slice(qi + 1).split("#")[0].split("&");
      for (var i = 0; i < parts.length; i++) {
        var kv = parts[i].split("="); var k = safeDecode(kv[0]);
        if (!(k in q)) q[k] = safeDecode(kv.slice(1).join("="));
      }
      if (!("v" in q) || !("mac" in q) || !("code" in q) || !("type" in q)) throw QrError("format", "missing field");
      if (q.v !== "1") throw QrError("version");
      mac = q.mac; code = q.code; typ = q.type;
    }
    var nm = normalizeMac(mac), nc = normalizeCode(code), ty = typ.toLowerCase();
    if (ty !== "flow" && ty !== "rain") throw QrError("type");
    return { mac: nm, code: nc, type: ty };
  }
  return { parse: parse, normalizeCode: normalizeCode, normalizeMac: normalizeMac, formatCode: formatCode, QrError: QrError };
});
