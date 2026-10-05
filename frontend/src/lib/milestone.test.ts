// 마일스톤 lib — 라벨 단일 표·배지 규칙(휴일 3분기·부분 수리)·D-N 문구·요청 본문·취소 409 detail (S3-2 PR-4b).

import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import {
  actualBody,
  cancelBlockers,
  changeKindLabel,
  customsBadge,
  derivedChanges,
  derivedReasonLabel,
  derivedText,
  dueText,
  holidayBadge,
  milestoneTypeLabel,
  planBody,
} from "./milestone";
import { exportBoard, milestoneRow } from "../test/milestone-fixtures";

const error = (code: string, detail: Record<string, unknown>) =>
  new ApiError(409, { code, message: "서버 문구", detail, requestId: null });

describe("라벨 단일 표", () => {
  it("종류 15종 한글, 모르는 값은 '기타'", () => {
    expect(milestoneTypeLabel("DOC_CUTOFF")).toBe("서류마감");
    expect(milestoneTypeLabel("PRESENTATION_DEADLINE")).toBe("L/C 제시기한");
    expect(milestoneTypeLabel("OUTGOING_INSPECTION")).toBe("출하검사");
    expect(milestoneTypeLabel("SOMETHING_NEW")).toBe("기타");
  });

  it("UNKNOWN 사유 한글 — 서버 DueReason 전체, 모르는 코드는 '산정 불가(기타)'(빈칸·0·'-' 아님)", () => {
    const unknown = (reason_code: string | null) => derivedText({ status: "UNKNOWN", value: null, basis: null, reason_code });
    expect(unknown("ANCHOR_PENDING")).toBe("산정 불가 — 기산일 미확정");
    expect(unknown("LC_TERMS_NOT_REGISTERED")).toBe("산정 불가 — L/C 조건 미등록");
    expect(unknown("NOT_CLEARED")).toBe("산정 불가 — 신고수리 전");
    expect(unknown("TERMS_MISSING")).toBe("산정 불가 — 결제조건 없음");
    expect(unknown("INVOICE_NOT_ISSUED")).toMatch(/^산정 불가 — 인보이스 미발행/);
    expect(unknown("RECEIPT_NOT_RECORDED")).toBe("산정 불가 — 입고 미기록");
    expect(unknown("DATE_OUT_OF_RANGE")).toBe("산정 불가 — 날짜 범위 밖(2000~2999년)");
    expect(unknown("TZ_UNRESOLVED")).toBe("산정 불가 — 시간대 확인 불가");
    expect(unknown("SOMETHING_NEW")).toBe("산정 불가(기타)");
    expect(unknown(null)).toBe("산정 불가(기타)");
    for (const code of ["LC_TENOR_UNSUPPORTED", "LC_INPUT_MISSING", "BL_MISSING", "EXPIRY_MISSING", "NO_BALANCE"]) {
      expect(derivedReasonLabel(code), code).not.toBeNull();
    }
    expect(derivedText({ status: "NOT_APPLICABLE", value: null, basis: null, reason_code: "NO_BALANCE" })).toBe(
      "해당 없음 — 잔금 없음(전액 선수금)",
    );
    expect(derivedText({ status: "OK", value: "2026-11-02", basis: "PLANNED", reason_code: null })).toBe("2026-11-02");
  });

  it("변경 종류 — PLAN_CHANGED는 ETD·ETA·Cargo Closing만 '롤오버'", () => {
    expect(changeKindLabel("PLAN_CHANGED", "ETA")).toBe("롤오버");
    expect(changeKindLabel("PLAN_CHANGED", "CARGO_CLOSING")).toBe("롤오버");
    expect(changeKindLabel("PLAN_CHANGED", "FILLING")).toBe("계획 변경");
    expect(changeKindLabel("PLAN_SET", "ETA")).toBe("계획 설정");
    expect(changeKindLabel("ACTUAL_CORRECTED", "ETD")).toBe("실적 정정");
    expect(changeKindLabel("WHAT", "ETD")).toBe("기타");
  });
});

describe("휴일 배지 3분기 (R-28 — DoD② 화면 층)", () => {
  const eta = (holiday: Parameters<typeof holidayBadge>[0]["holiday"]) =>
    holidayBadge({ holiday, effective: { value: "2027-01-04", basis: "PLANNED" } });

  it("도착국 휴일 → 경고 배지 + 휴일 이름", () => {
    expect(eta({ flag: "HOLIDAY", country: "US", name: "Observed New Year" })).toEqual({
      tone: "warn",
      text: "도착국 휴일: Observed New Year (US)",
    });
  });

  it("미선언 → '휴일 캘린더 미등록 — 확인 불가' + 그 국가·연도 캘린더 링크(연도는 문자열 앞 4자리)", () => {
    expect(eta({ flag: "UNVERIFIED", country: "JP", name: null })).toEqual({
      tone: "muted",
      text: "휴일 캘린더 미등록 — 확인 불가 (JP 2027)",
      link: "/holidays?country=JP&year=2027",
    });
  });

  it("CLEAR → 배지 0, 판정 없음(null) → 배지 0, 모르는 판정 → 확인 불가 쪽(fail-visible)", () => {
    expect(eta({ flag: "CLEAR", country: "US", name: null })).toBeNull();
    expect(eta(null)).toBeNull();
    expect(eta({ flag: "WHAT" as "CLEAR", country: "US", name: null })?.text).toMatch(/확인 불가/);
  });
});

describe("부분 수리 배지 (R-06)", () => {
  it("PARTIAL → '일부 미수리 n건', CLEARED·NONE → 배지 0", () => {
    expect(customsBadge({ customs_state: "PARTIAL", customs_pending_count: 2 })).toEqual({ tone: "warn", text: "일부 미수리 2건" });
    expect(customsBadge({ customs_state: "CLEARED", customs_pending_count: 0 })).toBeNull();
    expect(customsBadge({ customs_state: "NONE", customs_pending_count: 0 })).toBeNull();
    expect(customsBadge({ customs_state: null, customs_pending_count: null })).toBeNull();
  });
});

describe("D-N 문구 — 서버 days_left·is_overdue만", () => {
  it("열린 기일: D-3 / D-day / 도과 n일 지남, 실적 있으면 '완료'", () => {
    expect(dueText(milestoneRow("ETA", { planned: "2026-10-07", days_left: 3, is_overdue: false }))).toBe("D-3");
    expect(dueText(milestoneRow("ETA", { planned: "2026-10-04", days_left: 0, is_overdue: false }))).toBe("D-day");
    expect(dueText(milestoneRow("ETA", { planned: "2026-10-01", days_left: -3, is_overdue: true }))).toBe("3일 지남");
    expect(dueText(milestoneRow("ETA", { planned: "2026-10-01", actual: "2026-10-02" }))).toBe("완료");
  });

  it("시각형 같은 날 기한 시각이 지남 → '기한 지남', 판정 불가(TZ_UNRESOLVED) → 문구 없음(KST 추정 0)", () => {
    expect(dueText(milestoneRow("DOC_CUTOFF", { days_left: 0, is_overdue: true }))).toBe("기한 지남");
    expect(dueText(milestoneRow("DOC_CUTOFF", { unknown_reason: "TZ_UNRESOLVED", days_left: null }))).toBeNull();
  });

  it("대금만기(is_overdue null — 입금 확인 전) 음수 = '경과(도과 판정 없음)', 부분 수리 = '완료' 아님", () => {
    expect(dueText(milestoneRow("PAYMENT_DUE", { days_left: -2, is_overdue: null }))).toBe("2일 경과(도과 판정 없음)");
    expect(
      dueText(milestoneRow("CUSTOMS_CLEARED", { actual: "2026-10-02", customs_state: "PARTIAL", customs_pending_count: 1 })),
    ).toBeNull();
  });
});

describe("요청 본문 — 서버 스키마 그대로", () => {
  it("행이 없으면 version 생략, 있으면 연 순간의 version, 사유는 있을 때만", () => {
    expect(planBody(milestoneRow("ETA"), { on: "2026-11-01" }, null)).toEqual({ planned_on: "2026-11-01" });
    expect(planBody(milestoneRow("ETA", { milestone_id: 61, version: 3 }), { on: "2026-11-05" }, "선사 변경")).toEqual({
      planned_on: "2026-11-05",
      version: 3,
      reason: "선사 변경",
    });
    expect(planBody(milestoneRow("DOC_CUTOFF"), { at: "2026-11-05T05:00:00.000Z", tz: "Asia/Seoul" }, null)).toEqual({
      planned_at: "2026-11-05T05:00:00.000Z",
      tz: "Asia/Seoul",
    });
  });

  it("실적 지우기 = 명시적 null(시각형은 tz 없이), 값 키는 늘 있다", () => {
    expect(actualBody(milestoneRow("ETD", { milestone_id: 5, version: 2 }), null, "오입력")).toEqual({
      actual_on: null,
      version: 2,
      reason: "오입력",
    });
    expect(actualBody(milestoneRow("CARGO_CLOSING", { milestone_id: 6, version: 4 }), null, "오입력")).toEqual({
      actual_at: null,
      version: 4,
      reason: "오입력",
    });
    expect(actualBody(milestoneRow("ETD"), { on: "2026-10-04" }, null)).toEqual({ actual_on: "2026-10-04" });
  });
});

describe("선적 취소 409 detail", () => {
  it("통관 생존 = 신고번호 목록, 실적 생존 = 종류 목록, 다른 오류 = 빈 목록", () => {
    expect(cancelBlockers(error("SHIPMENTS.SHIPMENT.CUSTOMS_RECORD_ALIVE", { declaration_nos: ["AB-1001", 3] }))).toEqual({
      customs: ["AB-1001"],
      actuals: [],
    });
    expect(cancelBlockers(error("SHIPMENTS.SHIPMENT.ACTUAL_RECORDED", { milestone_types: ["ETD"] }))).toEqual({
      customs: [],
      actuals: ["ETD"],
    });
    expect(cancelBlockers(error("COMMON.CONCURRENCY.VERSION_CONFLICT", {}))).toEqual({ customs: [], actuals: [] });
    expect(cancelBlockers(new Error("x"))).toEqual({ customs: [], actuals: [] });
  });
});

describe("파생 재계산 안내(문자열 비교만)", () => {
  it("바뀐 파생 행만 안내, 같은 값·비적용 행은 조용히", () => {
    const before = exportBoard();
    const after = exportBoard({
      PAYMENT_DUE: { derived: { status: "OK", value: "2026-11-02", basis: "PLANNED", reason_code: null } },
    });
    expect(derivedChanges(before, after)).toEqual(["대금만기가 2026-11-02로 다시 계산됐습니다."]);
    expect(derivedChanges(after, after)).toEqual([]);
    expect(derivedChanges(undefined, after)).toEqual([]);
  });
});
