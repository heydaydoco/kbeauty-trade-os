// 시각 표시 — KST·현지 병기(design-D §D12, S3-2 PR-2b).

import { describe, expect, it } from "vitest";
import { toKstDisplay, toZonedPairDisplay } from "./datetime";

describe("toZonedPairDisplay", () => {
  it("KST와 현지 시각을 병기한다", () => {
    expect(toZonedPairDisplay("2026-11-05T05:00:00Z", "Asia/Tokyo")).toBe(
      "2026-11-05 14:00 (KST) · 2026-11-05 14:00 (Asia/Tokyo)",
    );
    expect(toZonedPairDisplay("2026-11-05T05:00:00Z", "Asia/Shanghai")).toBe(
      "2026-11-05 14:00 (KST) · 2026-11-05 13:00 (Asia/Shanghai)",
    );
  });

  it("현지 날짜가 KST와 다르면 날짜도 다르게 보인다(서쪽 시간대)", () => {
    expect(toZonedPairDisplay("2026-11-05T02:00:00+00:00", "America/Los_Angeles")).toBe(
      "2026-11-05 11:00 (KST) · 2026-11-04 18:00 (America/Los_Angeles)",
    );
  });

  it("Asia/Seoul이면 KST 하나만", () => {
    expect(toZonedPairDisplay("2026-11-05T05:00:00Z", "Asia/Seoul")).toBe("2026-11-05 14:00 (KST)");
  });

  it("자정은 24시가 아니라 00시", () => {
    expect(toZonedPairDisplay("2026-11-04T15:00:00Z", "Asia/Seoul")).toBe("2026-11-05 00:00 (KST)");
  });

  it("잘못된 시각·날짜 문자열은 '-', 잘못된 시간대는 KST만 + 확인 불가", () => {
    expect(toZonedPairDisplay("not-a-time", "Asia/Tokyo")).toBe("-");
    expect(toZonedPairDisplay("2026-11-05", "Asia/Tokyo")).toBe("-");
    expect(toZonedPairDisplay("2026-11-05T05:00:00Z", "Mars/Base")).toBe(
      "2026-11-05 14:00 (KST) · 현지 시각 확인 불가 (Mars/Base)",
    );
  });

  it("감사성 시각 표시(toKstDisplay)는 그대로", () => {
    expect(toKstDisplay("2026-11-05T05:00:00Z")).toContain("(KST)");
  });
});
