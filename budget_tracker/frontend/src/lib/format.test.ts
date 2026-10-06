import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import { amountInput, money, parseAmount, setBase, setLocale } from "./format";

// Same cases as the server (tests/amount_cases.json, also read by pytest)
const cases: [string, number][] = JSON.parse(readFileSync(new URL("../../../../tests/amount_cases.json", import.meta.url), "utf8"));

describe("parseAmount", () => {
  it.each(cases)("%s → %d", (text, expected) => {
    expect(parseAmount(text)).toBe(expected);
  });
  it("empty input is NaN", () => {
    expect(parseAmount("  ")).toBeNaN();
  });
});

describe("locale-aware amounts", () => {
  afterEach(() => {
    setLocale("en-US");
    setBase("USD");
  });
  it("last separator is the decimal when both appear", () => {
    expect(parseAmount("1,234.56")).toBe(1234.56);
    expect(parseAmount("1.234,56")).toBe(1234.56);
  });
  it("comma-grouped thousands in dot-decimal locales", () => {
    setLocale("en-US");
    expect(parseAmount("1,250")).toBe(1250);
    expect(parseAmount("12,500,000")).toBe(12500000);
    setLocale("de-DE");
    expect(parseAmount("1,250")).toBe(1.25);
  });
  it("strips currency symbols", () => {
    expect(parseAmount("$ 1,250.50")).toBe(1250.5);
    expect(parseAmount("12,50 €")).toBe(12.5);
  });
  it("amountInput round-trips in every locale", () => {
    for (const l of ["en-US", "de-DE", "tr-TR"]) {
      setLocale(l);
      for (const n of [0.5, 1.25, 12.5, 1250, 1250.5]) expect(parseAmount(amountInput(n))).toBe(n);
    }
  });
  it("money defaults to the base currency", () => {
    setBase("EUR");
    expect(money(1)).toContain("€");
    setBase("GBP");
    expect(money(1)).toContain("£");
  });
});
