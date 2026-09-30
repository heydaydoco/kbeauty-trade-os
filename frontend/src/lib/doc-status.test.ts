import { describe, expect, it } from "vitest";
import {
  canCancelQuotation,
  canEditQuotation,
  canIssueQuotation,
  canReviseQuotation,
  quotationStatusLabel,
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
