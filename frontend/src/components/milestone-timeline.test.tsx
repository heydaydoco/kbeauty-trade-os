// 마일스톤 타임라인 — 계획 PR-4b 검증 열(vitest): 파생 행 버튼 0 · CUSTOMS_CLEARED 실적 안내 · 부분 수리 배지 · UNKNOWN 사유 한글 ·
// ETA 휴일 배지 3분기(도착국 휴일 → 경고+이름 / 미선언 → '휴일 캘린더 미등록' / CLEAR → 배지 0) · 시각형 scan_date/local_date 구분 문구 (S3-2 PR-4b, 그룹 A·K).

import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { HolidaySummaryBanner, MilestoneTimeline } from "./milestone-timeline";
import { exportBoard, milestoneRow, oemBoard } from "../test/milestone-fixtures";
import { renderWithProviders } from "../test/render";
import type { MilestoneBoard } from "../lib/milestone";

function show(board: MilestoneBoard, canEdit = true) {
  const onEdit = vi.fn();
  renderWithProviders(
    <MilestoneTimeline board={board} canEdit={canEdit} onEdit={onEdit} customsSectionId="shipment-customs" label="마일스톤" />,
  );
  return onEdit;
}

const card = (name: string) => screen.getByRole("listitem", { name });

describe("마일스톤 타임라인 — 버튼(서버 allowed_actions → canEdit)", () => {
  it("파생 행(적재기한·대금만기·L/C 제시기한)은 편집 권한이 있어도 버튼 0, '자동 계산 — 직접 수정 불가'", () => {
    show(
      exportBoard({
        PRESENTATION_DEADLINE: {
          applicable: true,
          derived: { status: "UNKNOWN", value: null, basis: null, reason_code: "LC_TERMS_NOT_REGISTERED" },
        },
      }),
    );
    for (const name of ["적재기한", "대금만기", "L/C 제시기한"]) {
      expect(within(card(name)).queryAllByRole("button"), name).toHaveLength(0);
      expect(within(card(name)).getByText("자동 계산 — 직접 수정 불가")).toBeInTheDocument();
    }
    // 저장형은 계획·실적 버튼
    expect(within(card("ETD")).getByRole("button", { name: "계획 입력" })).toBeInTheDocument();
    expect(within(card("ETD")).getByRole("button", { name: "실적 입력" })).toBeInTheDocument();
  });

  it("편집 권한이 없으면(EDIT_MILESTONES 없음) 버튼이 하나도 없다 — 역할·상태를 화면이 다시 판정하지 않는다", () => {
    show(exportBoard(), false);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("CUSTOMS_CLEARED: 실적 버튼 대신 '통관 기록에서 입력합니다' 안내(통관 섹션 링크), 계획 버튼은 있다", () => {
    const onEdit = show(exportBoard());
    const customs = card("신고수리");
    expect(within(customs).queryByRole("button", { name: /실적/ })).not.toBeInTheDocument();
    expect(within(customs).getByRole("link", { name: "통관 기록에서 입력합니다 →" })).toHaveAttribute("href", "#shipment-customs");
    fireEvent.click(within(customs).getByRole("button", { name: "계획 입력" }));
    expect(onEdit).toHaveBeenCalledWith("plan", expect.objectContaining({ milestone_type: "CUSTOMS_CLEARED" }));
  });

  it("값이 있는 행은 '계획 변경'·'실적 정정', 누르면 그 순간의 행으로 대화상자를 연다", () => {
    const row = { planned: "2026-11-01", actual: "2026-10-30", milestone_id: 61, version: 3 };
    const onEdit = show(exportBoard({ ETA: row }));
    fireEvent.click(within(card("ETA")).getByRole("button", { name: "실적 정정" }));
    expect(onEdit).toHaveBeenCalledWith("actual", expect.objectContaining({ milestone_type: "ETA", version: 3 }));
    expect(within(card("ETA")).getByRole("button", { name: "계획 변경" })).toBeInTheDocument();
  });
});

describe("마일스톤 타임라인 — 판정 표시(서버 값만)", () => {
  it("부분 수리 배지 — PARTIAL이면 '일부 미수리 n건', 실적은 통관 기록 수리일, '완료'라고 말하지 않는다", () => {
    show(
      exportBoard({
        CUSTOMS_CLEARED: {
          actual: "2026-10-02",
          effective: { value: "2026-10-02", basis: "ACTUAL" },
          customs_state: "PARTIAL",
          customs_pending_count: 2,
        },
      }),
    );
    const customs = card("신고수리");
    expect(within(customs).getByText("일부 미수리 2건")).toBeInTheDocument();
    expect(within(customs).getByText("(통관 기록 수리일)")).toBeInTheDocument();
    expect(within(customs).queryByText("완료")).not.toBeInTheDocument();
  });

  it("CLEARED는 부분 수리 배지 0 + '완료'", () => {
    show(
      exportBoard({
        CUSTOMS_CLEARED: { actual: "2026-10-02", effective: { value: "2026-10-02", basis: "ACTUAL" }, customs_state: "CLEARED", customs_pending_count: 0 },
      }),
    );
    expect(within(card("신고수리")).queryByText(/미수리/)).not.toBeInTheDocument();
    expect(within(card("신고수리")).getByText("완료")).toBeInTheDocument();
  });

  it("UNKNOWN 사유 한글 — 빈칸·'-'·0일로 그리지 않는다, 모르는 사유는 '산정 불가(기타)'", () => {
    show(
      exportBoard({
        PAYMENT_DUE: { derived: { status: "UNKNOWN", value: null, basis: null, reason_code: "ANCHOR_PENDING" } },
        PRESENTATION_DEADLINE: {
          applicable: true,
          derived: { status: "UNKNOWN", value: null, basis: null, reason_code: "BRAND_NEW_CODE" },
        },
      }),
    );
    expect(within(card("대금만기")).getByText("산정 불가 — 기산일 미확정")).toBeInTheDocument();
    expect(within(card("적재기한")).getByText("산정 불가 — 신고수리 전")).toBeInTheDocument();
    expect(within(card("적재기한")).getByText("적재 이행: 판정 불가")).toBeInTheDocument();
    expect(within(card("L/C 제시기한")).getByText("산정 불가(기타)")).toBeInTheDocument();
    for (const name of ["대금만기", "적재기한", "L/C 제시기한"]) {
      expect(within(card(name)).queryByText(/^(-|0|D-0|0일)$/), name).not.toBeInTheDocument();
    }
    expect(within(card("대금만기")).getByText("휴일 미반영(자동 이월 없음).")).toBeInTheDocument();
  });

  it("파생 OK = 값 + '예정 기준(실적 입력 시 다시 계산)', D-N은 서버 days_left", () => {
    show(
      exportBoard({
        PAYMENT_DUE: { derived: { status: "OK", value: "2026-11-02", basis: "PLANNED", reason_code: null }, days_left: 29 },
      }),
    );
    const due = card("대금만기");
    expect(within(due).getByText("2026-11-02")).toBeInTheDocument();
    expect(within(due).getByText("예정 기준(실적 입력 시 다시 계산)")).toBeInTheDocument();
    expect(within(due).getByText("D-29")).toBeInTheDocument();
  });
});

describe("ETA 휴일 배지 3분기 (R-28 — DoD② 화면 층)", () => {
  const eta = (holiday: MilestoneBoard["rows"][number]["holiday"]) =>
    exportBoard({ ETA: { planned: "2027-01-04", effective: { value: "2027-01-04", basis: "PLANNED" }, days_left: 92, is_overdue: false, holiday } });

  it("도착국 휴일 → 경고 배지 + 휴일 이름(국가), 요약 배너 '휴일 경고 1건'", () => {
    const board = eta({ flag: "HOLIDAY", country: "US", name: "Observed New Year" });
    show(board);
    expect(within(card("ETA")).getByText("도착국 휴일: Observed New Year (US)")).toBeInTheDocument();
    renderWithProviders(<HolidaySummaryBanner summary={board.holiday_summary} />);
    expect(screen.getByRole("note")).toHaveTextContent("휴일 경고 1건");
  });

  it("미선언 → '휴일 캘린더 미등록 — 확인 불가' 배지 + 그 국가·연도 휴일 캘린더 링크", () => {
    show(eta({ flag: "UNVERIFIED", country: "JP", name: null }));
    const link = within(card("ETA")).getByRole("link", { name: /휴일 캘린더 미등록 — 확인 불가 \(JP 2027\)/ });
    expect(link).toHaveAttribute("href", "/holidays?country=JP&year=2027");
  });

  it("CLEAR → 배지 0(휴일 문구 없음), 요약 배너도 없음", () => {
    const board = eta({ flag: "CLEAR", country: "US", name: null });
    show(board);
    expect(within(card("ETA")).queryByText(/휴일/)).not.toBeInTheDocument();
    renderWithProviders(<HolidaySummaryBanner summary={board.holiday_summary} />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("ETA 외 종류에는 휴일 배지가 없다(R-09 — 서버가 ETA에만 준다)", () => {
    show(eta({ flag: "HOLIDAY", country: "US", name: "Observed New Year" }));
    expect(within(card("ETD")).queryByText(/휴일/)).not.toBeInTheDocument();
  });
});

describe("시각형(서류마감·Cargo Closing) — scan_date / local_date 구분 문구 (R-25)", () => {
  it("KST·현지 병기 + 'D-N 기준일(현지·한국 날짜 중 이른 날)'과 '현지 날짜'를 따로 적는다", () => {
    show(
      exportBoard({
        CARGO_CLOSING: {
          planned: { at_utc: "2026-10-10T23:00:00Z", tz: "America/Los_Angeles" },
          effective: { value: "2026-10-10T23:00:00+00:00", basis: "PLANNED" },
          scan_date: "2026-10-10",
          local_date: "2026-10-10",
          days_left: 6,
          is_overdue: false,
          milestone_id: 70,
          version: 1,
        },
        DOC_CUTOFF: {
          planned: { at_utc: "2026-10-11T03:00:00Z", tz: "Asia/Tokyo" },
          effective: { value: "2026-10-11T03:00:00+00:00", basis: "PLANNED" },
          scan_date: "2026-10-11",
          local_date: "2026-10-11",
          days_left: 7,
          is_overdue: false,
        },
      }),
    );
    const closing = card("Cargo Closing");
    expect(within(closing).getByText("2026-10-11 08:00 (KST) · 2026-10-10 16:00 (America/Los_Angeles)")).toBeInTheDocument();
    expect(within(closing).getByText("D-N 기준일 2026-10-10(현지·한국 날짜 중 이른 날)")).toBeInTheDocument();
    expect(within(closing).getByText("현지 날짜 2026-10-10")).toBeInTheDocument();
    expect(within(closing).getByText("D-6")).toBeInTheDocument();
    expect(within(card("서류마감")).getByText("현지 날짜 2026-10-11")).toBeInTheDocument();
  });

  it("날짜형 행에는 기준일·현지 날짜 문구가 없다(서버가 null) — 값은 문자열 그대로 + '(현지)'", () => {
    show(exportBoard({ ETD: { planned: "2026-10-20", effective: { value: "2026-10-20", basis: "PLANNED" }, days_left: 16, is_overdue: false } }));
    const etd = card("ETD");
    expect(within(etd).queryByText(/기준일/)).not.toBeInTheDocument();
    expect(within(etd).getByText("2026-10-20")).toBeInTheDocument();
    expect(within(etd).getByText("(현지)")).toBeInTheDocument();
    expect(within(etd).getByText("D-16")).toBeInTheDocument();
  });

  it("시간대 확인 불가(TZ_UNRESOLVED) → '시간대 확인 불가 — 판정 불가' 배지, D-N·기준일 없음(KST 추정 0)", () => {
    show(
      exportBoard({
        DOC_CUTOFF: {
          planned: { at_utc: "2026-10-11T03:00:00Z", tz: "Mars/Olympus" },
          effective: { value: "2026-10-11T03:00:00+00:00", basis: "PLANNED" },
          unknown_reason: "TZ_UNRESOLVED",
        },
      }),
    );
    const cutoff = card("서류마감");
    expect(within(cutoff).getByText("시간대 확인 불가 — 판정 불가")).toBeInTheDocument();
    expect(within(cutoff).queryByText(/^D-/)).not.toBeInTheDocument();
    expect(within(cutoff).queryByText(/기준일/)).not.toBeInTheDocument();
  });
});

describe("마일스톤 타임라인 — 그 밖의 배지·숨김", () => {
  it("롤오버 n회·통보 기록 없음 n·ETA<ETD 순서 경고·도과", () => {
    show(
      exportBoard({
        ETA: {
          planned: "2026-10-01",
          effective: { value: "2026-10-01", basis: "PLANNED" },
          days_left: -3,
          is_overdue: true,
          rollover_count: 2,
          unnotified_rollovers: 1,
          order_warning: "ETA_BEFORE_ETD",
        },
      }),
    );
    const eta = card("ETA");
    expect(within(eta).getByText("롤오버 2회")).toBeInTheDocument();
    expect(within(eta).getByText("통보 기록 없음 1")).toBeInTheDocument();
    expect(within(eta).getByText(/ETA가 ETD보다 이릅니다/)).toBeInTheDocument();
    expect(within(eta).getByText("3일 지남")).toBeInTheDocument();
    expect(within(eta).getByText("도과")).toBeInTheDocument();
  });

  it("해당 없는 종류(applicable=false)는 숨기고 몇 개 숨겼는지 말한다", () => {
    show(exportBoard());
    expect(screen.queryByRole("listitem", { name: "수입 세금 납부기한" })).not.toBeInTheDocument();
    expect(screen.queryByRole("listitem", { name: "L/C 제시기한" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(9);
    expect(screen.getByText(/해당하지 않는 종류 2개는 숨겼습니다/)).toBeInTheDocument();
  });

  it("OEM 보드 4행은 같은 컴포넌트 — 휴일·통관·롤오버 배지 0, 버튼은 canEdit만", () => {
    renderWithProviders(<MilestoneTimeline board={oemBoard()} canEdit onEdit={vi.fn()} label="생산 일정" />);
    expect(screen.getByRole("list", { name: "생산 일정" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((li) => li.getAttribute("aria-label"))).toEqual(["원료수급", "충진", "포장", "출하검사"]);
    expect(screen.queryByText(/휴일|미수리|롤오버|통보/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "계획 입력" })).toHaveLength(4);
  });

  it("행 공장(milestoneRow)은 서버 _empty_row 모양 — 파생은 input_source null", () => {
    expect(milestoneRow("PAYMENT_DUE").input_source).toBeNull();
    expect(milestoneRow("CUSTOMS_CLEARED").input_source).toBe("CUSTOMS_RECORD");
  });
});
