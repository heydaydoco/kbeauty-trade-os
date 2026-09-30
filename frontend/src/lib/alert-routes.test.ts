import { describe, expect, it } from "vitest";
import { alertRoute } from "./alert-routes";

describe("알림 이동 표", () => {
  it("quotations 알림은 견적 상세로 이동한다", () => {
    expect(alertRoute("quotations", 12)).toBe("/quotations/12");
  });

  it("표에 없는 종류·빈 값은 이동하지 않는다(없는 화면으로 보내지 않는다)", () => {
    expect(alertRoute("certifications", 3)).toBeNull();
    expect(alertRoute(null, null)).toBeNull();
    expect(alertRoute("quotations", null)).toBeNull();
  });
});
