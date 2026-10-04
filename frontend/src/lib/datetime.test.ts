// 시각 표시 — KST·현지 병기(design-D §D12, S3-2 PR-2b).

import { describe, expect, it } from "vitest";
import { timeZoneChoices, toKstDisplay, toZonedPairDisplay, utcToZonedWallTime, zonedWallTimeToUtc } from "./datetime";

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

describe("시각형 마일스톤 입력 변환 (S3-2 PR-4b — 벽시계 + IANA 시간대 ↔ UTC)", () => {
  it("벽시계 시각을 그 시간대의 UTC 시각으로(브라우저 시간대와 무관)", () => {
    expect(zonedWallTimeToUtc("2026-11-05T14:00", "Asia/Seoul")).toEqual({ ok: true, iso: "2026-11-05T05:00:00.000Z", ambiguous: false });
    expect(zonedWallTimeToUtc("2026-11-05T14:00", "Asia/Shanghai")).toEqual({ ok: true, iso: "2026-11-05T06:00:00.000Z", ambiguous: false });
    // 서쪽 시간대 — 현지 날짜가 UTC 날짜보다 하루 앞서 끝난다
    expect(zonedWallTimeToUtc("2026-11-04T18:00", "America/Los_Angeles")).toEqual({
      ok: true,
      iso: "2026-11-05T02:00:00.000Z",
      ambiguous: false,
    });
  });

  it("서머타임 앞당김으로 없는 시각은 거부, 되돌림으로 두 번 있는 시각은 이른 쪽 + ambiguous", () => {
    const gap = zonedWallTimeToUtc("2026-03-08T02:30", "America/New_York");
    expect(gap.ok).toBe(false);
    if (!gap.ok) expect(gap.problem).toMatch(/서머타임/);
    // 2026-11-01 01:30은 EDT(-4)와 EST(-5)에 두 번 — 이른 쪽 = EDT = 05:30Z
    expect(zonedWallTimeToUtc("2026-11-01T01:30", "America/New_York")).toEqual({
      ok: true,
      iso: "2026-11-01T05:30:00.000Z",
      ambiguous: true,
    });
  });

  it("형식 밖·모르는 시간대는 문구로 거부", () => {
    expect(zonedWallTimeToUtc("2026-11-05", "Asia/Seoul").ok).toBe(false);
    expect(zonedWallTimeToUtc("", "Asia/Seoul").ok).toBe(false);
    expect(zonedWallTimeToUtc("2026-11-05T14:00", "Mars/Olympus").ok).toBe(false);
  });

  it("UTC 시각 → 그 시간대 벽시계(datetime-local 초기값), 왕복 일치", () => {
    expect(utcToZonedWallTime("2026-11-05T05:00:00Z", "Asia/Tokyo")).toBe("2026-11-05T14:00");
    expect(utcToZonedWallTime("2026-11-05T02:00:00+00:00", "America/Los_Angeles")).toBe("2026-11-04T18:00");
    const wall = utcToZonedWallTime("2026-12-31T23:30:00Z", "Asia/Ho_Chi_Minh");
    expect(wall).toBe("2027-01-01T06:30");
    expect(zonedWallTimeToUtc(wall ?? "", "Asia/Ho_Chi_Minh")).toMatchObject({ ok: true, iso: "2026-12-31T23:30:00.000Z" });
    expect(utcToZonedWallTime("2026-11-05", "Asia/Seoul")).toBeNull(); // 날짜 문자열은 시각이 아니다
    expect(utcToZonedWallTime("2026-11-05T05:00:00Z", "Mars/Olympus")).toBeNull();
  });

  it("시간대 선택지 — 런타임 IANA 목록 + 저장된 값(목록 밖 정규 이름)을 잃지 않는다", () => {
    const names = timeZoneChoices();
    expect(names).toContain("Asia/Seoul");
    expect(names).toContain("America/New_York");
    expect(timeZoneChoices("Etc/Custom_Saved")[0]).toBe("Etc/Custom_Saved");
    expect(timeZoneChoices("Asia/Seoul").filter((name) => name === "Asia/Seoul")).toHaveLength(1);
  });
});
