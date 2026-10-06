import { describe, expect, it } from "vitest";
import { reportPeriod, transactionLink } from "./reporting";
describe("report periods and drilldown", () => {
  it("clamps calendar month to today and spans calendar months", () => {
    expect(reportPeriod("2026-10", 3, 1, "2026-10-05")).toEqual({ start: "2026-08-01", end: "2026-10-05" });
  });
  it("handles salary cycles and leap February", () => {
    expect(reportPeriod("2026-09", 1, 15, "2026-11-01")).toEqual({ start: "2026-09-15", end: "2026-10-14" });
    expect(reportPeriod("2024-02", 1, 1, "2026-10-05")).toEqual({ start: "2024-02-01", end: "2024-02-29" });
  });
  it("retains filters, uncategorized zero and Unicode merchant key", () => {
    const link = transactionLink("2026-09-15", "2026-10-05", "user_id=2&currency=USD", { category: 0, merchant_key: "crème brûlée" });
    const p = new URLSearchParams(link.split("?")[1]);
    expect(p.get("user_id")).toBe("2");
    expect(p.get("category")).toBe("0");
    expect(p.get("merchant_key")).toBe("crème brûlée");
    expect(p.get("start")).toBe("2026-09-15");
  });
});
