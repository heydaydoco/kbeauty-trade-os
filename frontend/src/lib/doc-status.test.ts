import { describe, expect, it } from "vitest";
import {
  canCancelQuotation,
  canEditQuotation,
  canIssueQuotation,
  canCancelProforma,
  canCreateProformaFrom,
  canReviseQuotation,
  proformaStatusLabel,
  quotationStatusLabel,
  statusBadgeClass,
} from "./doc-status";

describe("견적 상태별 가능 동작 (서버 전이표와 같아야 한다)", () => {
  it.each([
    ["DRAFT", true, true, true, false],
    ["ISSUED", false, false, true, true],
    ["CONVERTED", false, false, false, false],
    ["EXPIRED", false, false, false, false],
    ["CANCELLED", false, false, false, false],
  ])("%s", (status, edit, issue, cancel, revise) => {
    expect(canEditQuotation(status)).toBe(edit);
    expect(canIssueQuotation(status)).toBe(issue);
    expect(canCancelQuotation(status)).toBe(cancel);
    expect(canReviseQuotation(status)).toBe(revise);
  });

  it("모르는 상태 코드는 감추지 않고 그대로 보인다", () => {
    expect(quotationStatusLabel("WEIRD")).toBe("WEIRD");
    expect(quotationStatusLabel("ISSUED")).toBe("발행");
  });
});

describe("PI 상태별 가능 동작 (서버 전이표와 같아야 한다)", () => {
  it.each([
    ["ISSUED", true],
    ["PARTIALLY_PAID", false],
    ["PAID", false],
    ["EXPIRED", false],
    ["CANCELLED", false],
  ])("%s 취소 가능=%s", (status, cancel) => {
    expect(canCancelProforma(status)).toBe(cancel);
  });

  it("라벨·배지: 입금 상태는 진하게, 취소·만료는 취소선", () => {
    expect(proformaStatusLabel("PARTIALLY_PAID")).toBe("일부입금");
    expect(proformaStatusLabel("PAID")).toBe("입금완료");
    expect(proformaStatusLabel("WEIRD")).toBe("WEIRD");
    expect(statusBadgeClass("PAID")).toContain("bg-gray-900");
    expect(statusBadgeClass("CANCELLED")).toContain("line-through");
  });

  it.each([
    ["DRAFT", false],
    ["ISSUED", true],
    ["CONVERTED", true],
    ["EXPIRED", false],
    ["CANCELLED", false],
  ])("원천 QT %s → PI 만들기 가능=%s", (status, ok) => {
    expect(canCreateProformaFrom(status)).toBe(ok);
  });
});
