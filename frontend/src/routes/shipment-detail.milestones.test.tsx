// 선적 상세 — 마일스톤 화면 관통(S3-2 PR-4b, 그룹 A·H·J·K): 버튼 = allowed_actions(EDIT_MILESTONES·PLAN_DRAFT)만 · 계획 입력 본문(행 없으면
// version 생략) · 롤오버 사유 빈칸 제출 불가 · 롤오버 + 통보 2요청(실패해도 롤오버 유지·같은 키 재시도) · 실적 지우기(명시적 null) ·
// 처리 중 입력 잠금·Esc 무시·더블클릭 1회 · 409(VERSION_CONFLICT·DUPLICATE_TYPE) → 다시 불러오기 · 결과 모르는 실패 → 같은 키 ·
// 시각형 시간대(KR 출발 = Asia/Seoul 기본, 그 밖은 필수) · 계획 초안 · 취소 409(통관·실적 생존) 이어 주기 · 변경 이력 통보 · 파생 재계산 안내.
// fetch 스텁은 정확 URL·메서드 일치(stubGateFetch).

import { focusManager } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { stubGateFetch, type GateCall, type GateHandler } from "../test/gate-fixtures";
import { exportBoard, milestoneChange } from "../test/milestone-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";
import { soDetail, SO_LINE } from "../test/so-fixtures";
import { apiErrorResponse, shipmentDetail } from "../test/shipment-fixtures";
import type { MilestoneBoard, MilestoneChange, MilestoneRow } from "../lib/milestone";

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  focusManager.setFocused(undefined);
});

const SH = "/v1/shipments/31";
const BASE_ACTIONS = ["RELEASE_ORDER", "CANCEL", "EDIT_LINES", "EDIT_COUNTRIES", "EDIT_META", "EDIT_PARTIES"];
const ALL_ACTIONS = [...BASE_ACTIONS, "EDIT_MILESTONES", "PLAN_DRAFT", "EDIT_CUSTOMS"];
const ROLLED: Partial<MilestoneRow> = {
  planned: "2026-11-01",
  effective: { value: "2026-11-01", basis: "PLANNED" },
  days_left: 28,
  is_overdue: false,
  milestone_id: 61,
  version: 3,
};

function detail(boardOverrides: Record<string, Partial<MilestoneRow>> = {}, over: Parameters<typeof shipmentDetail>[0] = {}) {
  return shipmentDetail({ allowed_actions: ALL_ACTIONS, milestones: exportBoard(boardOverrides), ...over });
}

const server = { detail: detail(), changes: [] as MilestoneChange[] };

function refs(): GateHandler[] {
  return [
    [`${SH}/status-log`, "GET", () => jsonResponse(page([]))],
    ["/v1/document-flow/SHIPMENT/31", "GET", () => jsonResponse({ root_kind: "SALES_ORDER", root_id: 9, nodes: [] })],
    ["/v1/users/lookup?size=200", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }]))],
    ["/v1/sales-orders/9", "GET", () => jsonResponse(soDetail({ status: "IN_SHIPMENT", lines: [{ ...SO_LINE, shipment_open_quantity: 2 }] }))],
    [`${SH}/customs-records`, "GET", () => jsonResponse(page([]))],
    [`${SH}/milestone-changes`, "GET", () => jsonResponse(page(server.changes))],
    [SH, "GET", () => jsonResponse(server.detail)],
  ];
}

function open(initial = detail(), me: unknown = TRADER, extra: GateHandler[] = []) {
  server.detail = initial;
  const stub = stubGateFetch(me, [...extra, ...refs()]);
  renderWithProviders(<AppRoutes />, { route: "/shipments/31" });
  return stub;
}

const heading = () => screen.findByRole("heading", { name: /SH-2026-0001/ });
const sent = (calls: GateCall[], path: string, method: string) => calls.filter((c) => c.method === method && c.url === `/api${path}`);
const card = (name: string) => screen.getByRole("listitem", { name });
const dialog = () => screen.getByRole("dialog");
const written = (board: MilestoneBoard, change: { id: number; change_kind: string } | null = { id: 900, change_kind: "PLAN_SET" }) =>
  () => jsonResponse({ board, change });

describe("버튼 = 서버 allowed_actions만", () => {
  it("EDIT_MILESTONES·PLAN_DRAFT·EDIT_CUSTOMS가 있으면 카드 버튼·계획 초안·통관 추가가 보인다", async () => {
    open();
    await heading();
    expect(screen.getByRole("button", { name: "계획 초안 만들기" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "통관 기록 추가" })).toBeInTheDocument();
    expect(within(card("ETA")).getByRole("button", { name: "계획 입력" })).toBeInTheDocument();
  });

  it("서버가 마일스톤 동작을 주지 않으면(조회 역할·취소 선적) 같은 화면에 쓰기 버튼 0 — 화면이 역할을 다시 판정하지 않는다", async () => {
    open(detail({}, { allowed_actions: [] }), VIEWER);
    await heading();
    expect(within(screen.getByRole("list", { name: "마일스톤" })).queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /계획 초안|빠진 종류/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "통관 기록 추가" })).not.toBeInTheDocument();
  });

  it("무역 역할이어도 allowed_actions에 없으면 버튼 0(서버 판정이 정본)", async () => {
    open(detail({}, { allowed_actions: BASE_ACTIONS }), TRADER);
    await heading();
    expect(within(screen.getByRole("list", { name: "마일스톤" })).queryAllByRole("button")).toHaveLength(0);
  });
});

describe("계획 입력·롤오버", () => {
  it("행 없는 ETA 계획 입력 → {planned_on}만(version 생략)·멱등 키, 응답 보드로 카드 갱신(헤더 기준 version 무변경)", async () => {
    const after = detail({ ETA: ROLLED }).milestones;
    const stub = open(detail(), TRADER, [[`${SH}/milestones/ETA/plan`, "POST", written(after)]]);
    await heading();
    fireEvent.click(within(card("ETA")).getByRole("button", { name: "계획 입력" }));
    expect(within(dialog()).getByRole("heading", { name: "계획 입력 — ETA" })).toBeInTheDocument();
    fireEvent.change(within(dialog()).getByLabelText(/계획일/), { target: { value: "2026-11-01" } });
    fireEvent.click(within(dialog()).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const [call] = sent(stub.calls, `${SH}/milestones/ETA/plan`, "POST");
    expect(call?.body).toEqual({ planned_on: "2026-11-01" });
    expect(call?.headers["Idempotency-Key"]).toMatch(/^key-\d+$/);
    expect(await within(card("ETA")).findByText("2026-11-01")).toBeInTheDocument();
    expect(within(card("ETA")).getByRole("button", { name: "계획 변경" })).toBeInTheDocument();
    expect(screen.queryByText(/다른 곳에서 이 선적이 수정되었습니다/)).not.toBeInTheDocument();
  });

  it("롤오버(기존 계획 변경): 사유 빈칸·공백만이면 저장 불가 → 사유를 넣어야 {planned_on, version(연 순간), reason}", async () => {
    const stub = open(detail({ ETA: ROLLED }), TRADER, [
      [`${SH}/milestones/ETA/plan`, "POST", written(detail({ ETA: { ...ROLLED, planned: "2026-11-05", version: 4 } }).milestones)],
    ]);
    await heading();
    fireEvent.click(within(card("ETA")).getByRole("button", { name: "계획 변경" }));
    const box = dialog();
    expect(within(box).getByRole("heading", { name: "계획 변경(롤오버) — ETA" })).toBeInTheDocument();
    expect(within(box).getByText("2026-11-01")).toBeInTheDocument(); // 이전 계획
    fireEvent.change(within(box).getByLabelText(/계획일/), { target: { value: "2026-11-05" } });
    const save = within(box).getByRole("button", { name: "저장" });
    expect(save).toBeDisabled();
    fireEvent.change(within(box).getByLabelText("변경 사유 (필수)"), { target: { value: "   " } });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(sent(stub.calls, `${SH}/milestones/ETA/plan`, "POST")).toHaveLength(0);
    fireEvent.change(within(box).getByLabelText("변경 사유 (필수)"), { target: { value: " 선사 스케줄 변경 " } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(sent(stub.calls, `${SH}/milestones/ETA/plan`, "POST")).toHaveLength(1));
    expect(sent(stub.calls, `${SH}/milestones/ETA/plan`, "POST")[0]?.body).toEqual({
      planned_on: "2026-11-05",
      version: 3,
      reason: "선사 스케줄 변경",
    });
  });

  it("같은 값이면 '바뀐 값이 없습니다' — 저장 불가(no-op 요청 0)", async () => {
    open(detail({ ETA: ROLLED }));
    await heading();
    fireEvent.click(within(card("ETA")).getByRole("button", { name: "계획 변경" }));
    fireEvent.change(within(dialog()).getByLabelText("변경 사유 (필수)"), { target: { value: "사유" } });
    expect(within(dialog()).getByText("바뀐 값이 없습니다.")).toBeInTheDocument();
    expect(within(dialog()).getByRole("button", { name: "저장" })).toBeDisabled();
  });

  it("롤오버 + '통보도 지금 기록' = 요청 2회(각자 키) — 계획 → 응답 change.id로 통보, '보내기'가 아니라 기록", async () => {
    const stub = open(detail({ ETA: ROLLED }), TRADER, [
      [`${SH}/milestones/ETA/plan`, "POST", written(detail({ ETA: { ...ROLLED, planned: "2026-11-05" } }).milestones, { id: 905, change_kind: "PLAN_CHANGED" })],
      [`${SH}/milestone-changes/905/notices`, "POST", () => jsonResponse(milestoneChange({ id: 905 }), 201)],
    ]);
    await heading();
    fireEvent.click(within(card("ETA")).getByRole("button", { name: "계획 변경" }));
    const box = dialog();
    fireEvent.change(within(box).getByLabelText(/계획일/), { target: { value: "2026-11-05" } });
    fireEvent.change(within(box).getByLabelText("변경 사유 (필수)"), { target: { value: "선사 변경" } });
    fireEvent.click(within(box).getByLabelText("통보도 지금 기록"));
    expect(within(box).getByText("이 시스템은 메일을 보내지 않습니다. 실제로 알린 사실을 기록합니다.")).toBeInTheDocument();
    expect(within(box).queryByText(/보내기/)).not.toBeInTheDocument();
    const save = within(box).getByRole("button", { name: "변경 + 통보 기록 저장" });
    expect(save).toBeDisabled(); // 요지 필수
    fireEvent.change(within(box).getByLabelText("요지 (필수)"), { target: { value: "포워더에 메일로 알림\n회신 대기" } });
    fireEvent.click(save);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const plan = sent(stub.calls, `${SH}/milestones/ETA/plan`, "POST");
    const notice = sent(stub.calls, `${SH}/milestone-changes/905/notices`, "POST");
    expect(plan).toHaveLength(1);
    expect(notice).toHaveLength(1);
    expect(notice[0]?.body).toEqual({ occurred_on: "2026-10-04", summary: "포워더에 메일로 알림\n회신 대기" });
    expect(notice[0]?.headers["Idempotency-Key"]).not.toBe(plan[0]?.headers["Idempotency-Key"]);
  });

  it("통보 기록이 실패해도 롤오버는 남는다 — 실패 안내 + '다시 시도'는 같은 키", async () => {
    let fail = true;
    const stub = open(detail({ ETA: ROLLED }), TRADER, [
      [`${SH}/milestones/ETA/plan`, "POST", written(detail({ ETA: { ...ROLLED, planned: "2026-11-05" } }).milestones, { id: 905, change_kind: "PLAN_CHANGED" })],
      [
        `${SH}/milestone-changes/905/notices`,
        "POST",
        () => (fail ? jsonResponse(apiErrorResponse("COMMON.SERVER.ERROR", "일시 오류입니다."), 503) : jsonResponse(milestoneChange(), 201)),
      ],
    ]);
    await heading();
    fireEvent.click(within(card("ETA")).getByRole("button", { name: "계획 변경" }));
    fireEvent.change(within(dialog()).getByLabelText(/계획일/), { target: { value: "2026-11-05" } });
    fireEvent.change(within(dialog()).getByLabelText("변경 사유 (필수)"), { target: { value: "선사 변경" } });
    fireEvent.click(within(dialog()).getByLabelText("통보도 지금 기록"));
    fireEvent.change(within(dialog()).getByLabelText("요지 (필수)"), { target: { value: "전화로 알림" } });
    fireEvent.click(within(dialog()).getByRole("button", { name: "변경 + 통보 기록 저장" }));
    expect(await screen.findByText(/롤오버는 기록됐고 통보 기록은 실패했습니다/)).toBeInTheDocument();
    expect(await within(card("ETA")).findByText("2026-11-05")).toBeInTheDocument(); // 롤오버는 화면에 반영
    await waitFor(() => expect(document.activeElement).toHaveTextContent("ETA — 통보 기록 실패"));
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const notice = sent(stub.calls, `${SH}/milestone-changes/905/notices`, "POST");
    expect(notice).toHaveLength(2);
    expect(notice[1]?.headers["Idempotency-Key"]).toBe(notice[0]?.headers["Idempotency-Key"]);
  });
});

describe("실적 입력·정정", () => {
  it("실적 지우기(정정) = {actual_on: null, version, reason} — 사유 없으면 저장 불가", async () => {
    const etd = { ...ROLLED, actual: "2026-10-03", milestone_id: 55, version: 2 };
    const stub = open(detail({ ETD: etd }, { status: "RELEASE_ORDERED" }), TRADER, [
      [`${SH}/milestones/ETD/actual`, "POST", written(detail({ ETD: { ...etd, actual: null, version: 3 } }).milestones, { id: 901, change_kind: "ACTUAL_CORRECTED" })],
    ]);
    await heading();
    fireEvent.click(within(card("ETD")).getByRole("button", { name: "실적 정정" }));
    const box = dialog();
    expect(within(box).getByRole("heading", { name: "실적 정정 — ETD" })).toBeInTheDocument();
    fireEvent.click(within(box).getByLabelText(/실적 지우기/));
    expect(within(box).getByLabelText(/실적일/)).toBeDisabled();
    expect(within(box).getByRole("button", { name: "저장" })).toBeDisabled();
    fireEvent.change(within(box).getByLabelText("정정 사유 (필수)"), { target: { value: "오입력" } });
    fireEvent.click(within(box).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(sent(stub.calls, `${SH}/milestones/ETD/actual`, "POST")).toHaveLength(1));
    expect(sent(stub.calls, `${SH}/milestones/ETD/actual`, "POST")[0]?.body).toEqual({ actual_on: null, version: 2, reason: "오입력" });
  });

  it("실적 입력 뒤 파생 행이 다시 계산되면 aria-live로 알린다(문자열 비교만)", async () => {
    const after = detail({
      ETD: { actual: "2026-10-03", effective: { value: "2026-10-03", basis: "ACTUAL" }, milestone_id: 55, version: 1 },
      PAYMENT_DUE: { derived: { status: "OK", value: "2026-11-02", basis: "ACTUAL", reason_code: null }, days_left: 29 },
    }).milestones;
    open(detail({}, { status: "RELEASE_ORDERED" }), TRADER, [[`${SH}/milestones/ETD/actual`, "POST", written(after, { id: 902, change_kind: "ACTUAL_RECORDED" })]]);
    await heading();
    fireEvent.click(within(card("ETD")).getByRole("button", { name: "실적 입력" }));
    fireEvent.change(within(dialog()).getByLabelText(/실적일/), { target: { value: "2026-10-03" } });
    fireEvent.click(within(dialog()).getByRole("button", { name: "저장" }));
    expect(await screen.findByText("대금만기가 2026-11-02로 다시 계산됐습니다.")).toBeInTheDocument();
    expect(screen.getByText("대금만기가 2026-11-02로 다시 계산됐습니다.").closest("[role=status]")).toHaveAttribute("aria-live", "polite");
  });
});

describe("동시성·멱등 — 처리 중 잠금·409·결과 모르는 실패", () => {
  it("처리 중에는 입력 잠금·Esc 무시·더블클릭 1회 — 응답이 오면 결과로 닫힌다", async () => {
    let release: (value: Response) => void = () => undefined;
    const stub = open(detail(), TRADER, [
      [
        `${SH}/milestones/ETA/plan`,
        "POST",
        () =>
          new Promise<Response>((resolve) => {
            release = resolve;
          }),
      ],
    ]);
    await heading();
    fireEvent.click(within(card("ETA")).getByRole("button", { name: "계획 입력" }));
    fireEvent.change(within(dialog()).getByLabelText(/계획일/), { target: { value: "2026-11-01" } });
    const save = within(dialog()).getByRole("button", { name: "저장" });
    fireEvent.click(save);
    fireEvent.click(save);
    await waitFor(() => expect(within(dialog()).getByRole("button", { name: "처리 중…" })).toBeDisabled());
    expect(within(dialog()).getByLabelText(/계획일/)).toBeDisabled();
    expect(within(dialog()).getByRole("button", { name: "닫기" })).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(sent(stub.calls, `${SH}/milestones/ETA/plan`, "POST")).toHaveLength(1);
    release(jsonResponse({ board: detail({ ETA: ROLLED }).milestones, change: { id: 900, change_kind: "PLAN_SET" } }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it.each([
    ["COMMON.CONCURRENCY.VERSION_CONFLICT", "다른 사용자가 먼저 수정했습니다."],
    ["SHIPMENTS.MILESTONE.DUPLICATE_TYPE", "이 종류의 마일스톤이 이미 있습니다."],
  ])("겹친 편집 409(%s) → 대화상자 안 '최신 내용 불러오기' → 닫고 상세(보드)를 다시 받는다", async (code, message) => {
    const stub = open(detail(), TRADER, [[`${SH}/milestones/ETA/plan`, "POST", () => jsonResponse(apiErrorResponse(code, message), 409)]]);
    await heading();
    const before = sent(stub.calls, SH, "GET").length;
    fireEvent.click(within(card("ETA")).getByRole("button", { name: "계획 입력" }));
    fireEvent.change(within(dialog()).getByLabelText(/계획일/), { target: { value: "2026-11-01" } });
    fireEvent.click(within(dialog()).getByRole("button", { name: "저장" }));
    const reload = await within(dialog()).findByRole("button", { name: "최신 내용 불러오기" });
    if (code === "SHIPMENTS.MILESTONE.DUPLICATE_TYPE") expect(within(dialog()).getByText(message)).toBeInTheDocument(); // 서버 문구 그대로
    fireEvent.click(reload);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(sent(stub.calls, SH, "GET").length).toBeGreaterThan(before));
  });

  it("결과를 모르는 실패(503) → 같은 키 재시도 안내, 다시 누르면 같은 키", async () => {
    let fail = true;
    const stub = open(detail(), TRADER, [
      [
        `${SH}/milestones/ETA/plan`,
        "POST",
        () => (fail ? jsonResponse(apiErrorResponse("COMMON.SERVER.ERROR", "일시 오류입니다."), 503) : written(detail({ ETA: ROLLED }).milestones)()),
      ],
    ]);
    await heading();
    fireEvent.click(within(card("ETA")).getByRole("button", { name: "계획 입력" }));
    fireEvent.change(within(dialog()).getByLabelText(/계획일/), { target: { value: "2026-11-01" } });
    fireEvent.click(within(dialog()).getByRole("button", { name: "저장" }));
    expect(await within(dialog()).findByText(/같은 요청\(같은 키\)으로 결과를 확인합니다/)).toBeInTheDocument();
    fail = false;
    fireEvent.click(within(dialog()).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const calls = sent(stub.calls, `${SH}/milestones/ETA/plan`, "POST");
    expect(calls).toHaveLength(2);
    expect(calls[1]?.headers["Idempotency-Key"]).toBe(calls[0]?.headers["Idempotency-Key"]);
  });

  it("대화상자를 연 뒤 보드가 새로 와도(다른 곳의 변경) 보내는 version은 연 순간의 값 — 점프 0, 서버 409가 판정", async () => {
    const stub = open(detail({ ETA: ROLLED }), TRADER, [[`${SH}/milestones/ETA/plan`, "POST", written(detail({ ETA: ROLLED }).milestones)]]);
    await heading();
    fireEvent.click(within(card("ETA")).getByRole("button", { name: "계획 변경" }));
    server.detail = detail({ ETA: { ...ROLLED, planned: "2026-11-09", version: 7 } });
    // 창 포커스 재조회(staleTime 0) — 뒤의 보드가 새 값으로 바뀐다
    const before = sent(stub.calls, SH, "GET").length;
    act(() => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    await waitFor(() => expect(sent(stub.calls, SH, "GET").length).toBeGreaterThan(before));
    expect(await within(card("ETA")).findByText("2026-11-09")).toBeInTheDocument();
    fireEvent.change(within(dialog()).getByLabelText(/계획일/), { target: { value: "2026-11-05" } });
    fireEvent.change(within(dialog()).getByLabelText("변경 사유 (필수)"), { target: { value: "사유" } });
    fireEvent.click(within(dialog()).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(sent(stub.calls, `${SH}/milestones/ETA/plan`, "POST")).toHaveLength(1));
    expect(sent(stub.calls, `${SH}/milestones/ETA/plan`, "POST")[0]?.body).toMatchObject({ version: 3 });
  });
});

describe("시각형(서류마감·Cargo Closing) — 벽시계 + IANA 시간대", () => {
  it("출발국 KR이면 시간대 기본 Asia/Seoul — {planned_at: UTC ISO, tz}·저장될 시각 미리보기", async () => {
    const stub = open(detail(), TRADER, [[`${SH}/milestones/DOC_CUTOFF/plan`, "POST", written(detail().milestones)]]);
    await heading();
    fireEvent.click(within(card("서류마감")).getByRole("button", { name: "계획 입력" }));
    const box = dialog();
    expect(within(box).getByLabelText("시간대 (IANA, 필수)")).toHaveValue("Asia/Seoul");
    fireEvent.change(within(box).getByLabelText(/계획 시각/), { target: { value: "2026-10-10T17:00" } });
    expect(within(box).getByText(/저장될 시각: 2026-10-10 17:00 \(KST\)/)).toBeInTheDocument();
    fireEvent.click(within(box).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(sent(stub.calls, `${SH}/milestones/DOC_CUTOFF/plan`, "POST")).toHaveLength(1));
    expect(sent(stub.calls, `${SH}/milestones/DOC_CUTOFF/plan`, "POST")[0]?.body).toEqual({
      planned_at: "2026-10-10T08:00:00.000Z",
      tz: "Asia/Seoul",
    });
  });

  it("출발국이 KR이 아니면 시간대 기본값 없음·필수 — 고르기 전엔 저장 불가", async () => {
    const stub = open(detail({}, { origin_country_code: "US", dest_country_code: "KR" }), TRADER, [
      [`${SH}/milestones/CARGO_CLOSING/plan`, "POST", written(detail().milestones)],
    ]);
    await heading();
    fireEvent.click(within(card("Cargo Closing")).getByRole("button", { name: "계획 입력" }));
    const box = dialog();
    expect(within(box).getByLabelText("시간대 (IANA, 필수)")).toHaveValue("");
    fireEvent.change(within(box).getByLabelText(/계획 시각/), { target: { value: "2026-10-10T16:00" } });
    expect(within(box).getByRole("button", { name: "저장" })).toBeDisabled();
    fireEvent.change(within(box).getByLabelText("시간대 (IANA, 필수)"), { target: { value: "America/Los_Angeles" } });
    fireEvent.click(within(box).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(sent(stub.calls, `${SH}/milestones/CARGO_CLOSING/plan`, "POST")).toHaveLength(1));
    expect(sent(stub.calls, `${SH}/milestones/CARGO_CLOSING/plan`, "POST")[0]?.body).toEqual({
      planned_at: "2026-10-10T23:00:00.000Z",
      tz: "America/Los_Angeles",
    });
  });

  it("실적이 이미 있으면 계획 시간대는 그 시간대로 고정(같은 장소의 사건 — 서버 422 사전 차단)", async () => {
    open(
      detail({
        DOC_CUTOFF: {
          planned: { at_utc: "2026-10-10T07:00:00Z", tz: "Asia/Tokyo" },
          actual: { at_utc: "2026-10-03T07:00:00Z", tz: "Asia/Tokyo" },
          milestone_id: 71,
          version: 2,
        },
      }),
    );
    await heading();
    fireEvent.click(within(card("서류마감")).getByRole("button", { name: "계획 변경" }));
    const zone = within(dialog()).getByLabelText("시간대 (IANA, 필수)");
    expect(zone).toHaveValue("Asia/Tokyo");
    expect(zone).toBeDisabled();
    expect(within(dialog()).getByLabelText(/계획 시각/)).toHaveValue("2026-10-10T16:00");
  });
});

describe("계획 초안·선적 취소 409 이어 주기", () => {
  it("계획 초안 = 본문 {}·멱등 키 → 응답 보드 반영, 이후 라벨 '빠진 종류 채우기'", async () => {
    const after = detail({ ETD: { milestone_id: 55, version: 1 }, ETA: { milestone_id: 56, version: 1 } }).milestones;
    const stub = open(detail(), TRADER, [[`${SH}/milestones/plan-draft`, "POST", () => jsonResponse(after)]]);
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "계획 초안 만들기" }));
    expect(await screen.findByRole("button", { name: "빠진 종류 채우기" })).toBeInTheDocument();
    const [call] = sent(stub.calls, `${SH}/milestones/plan-draft`, "POST");
    expect(call?.body).toEqual({});
    expect(call?.headers["Idempotency-Key"]).toBeTruthy();
  });

  it("취소 409 통관 생존 → 서버 문구 + 먼저 삭제할 신고번호 + '통관 기록으로 가기'(대화상자 닫고 통관 섹션으로)", async () => {
    open(detail(), TRADER, [
      [
        `${SH}/transitions`,
        "POST",
        () =>
          jsonResponse(
            apiErrorResponse("SHIPMENTS.SHIPMENT.CUSTOMS_RECORD_ALIVE", "통관 기록이 남아 있는 선적은 취소할 수 없습니다.", {
              declaration_nos: ["AB-1001", "AB-1002"],
            }),
            409,
          ),
      ],
    ]);
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    fireEvent.change(screen.getByLabelText("취소 사유 (필수)"), { target: { value: "고객 요청" } });
    fireEvent.click(screen.getByRole("button", { name: "취소 확정" }));
    expect(await screen.findByText("먼저 삭제할 통관 기록: AB-1001, AB-1002")).toBeInTheDocument();
    expect(screen.getByText(/통관 기록이 남아 있는 선적은 취소할 수 없습니다/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "통관 기록으로 가기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("heading", { name: "통관 기록" })));
  });

  it("취소 409 실적 생존 → 정정할 실적 종류(한글) + '마일스톤으로 가기'", async () => {
    open(detail(), TRADER, [
      [
        `${SH}/transitions`,
        "POST",
        () =>
          jsonResponse(
            apiErrorResponse("SHIPMENTS.SHIPMENT.ACTUAL_RECORDED", "실적이 기록된 선적은 취소할 수 없습니다.", { milestone_types: ["ETD", "BL_ISSUED"] }),
            409,
          ),
      ],
    ]);
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    fireEvent.change(screen.getByLabelText("취소 사유 (필수)"), { target: { value: "고객 요청" } });
    fireEvent.click(screen.getByRole("button", { name: "취소 확정" }));
    expect(await screen.findByText("먼저 정정(지우기)할 실적: ETD, B/L(AWB) 발행일")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "마일스톤으로 가기" }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("heading", { name: "마일스톤" })));
  });
});

describe("마일스톤 변경 이력·통보", () => {
  it("롤오버 이력: '롤오버'·이전→새 값·사유·기록자·KST 시각, 통보 없으면 '통보 기록 없음' 배지 + '통보 기록' → POST 본문", async () => {
    server.changes = [milestoneChange(), milestoneChange({ id: 899, milestone_type: "PSI", change_kind: "PLAN_CHANGED", reason: "검사 일정" })];
    const stub = open(detail(), TRADER, [[`${SH}/milestone-changes/900/notices`, "POST", () => jsonResponse(milestoneChange({ notices: [] }), 201)]]);
    await heading();
    const history = screen.getByRole("region", { name: "마일스톤 변경 이력" });
    const row = (await within(history).findByText("선사 스케줄 변경")).closest("tr") as HTMLElement;
    expect(within(row).getByText("롤오버")).toBeInTheDocument();
    expect(within(row).getByText("2026-11-01")).toBeInTheDocument();
    expect(within(row).getByText("2026-11-05")).toBeInTheDocument();
    expect(within(row).getByText("2026. 10. 04. 12:00 (KST)")).toBeInTheDocument();
    expect(within(row).getByText("통보 기록 없음")).toBeInTheDocument();
    const psi = within(history).getByText("검사 일정").closest("tr") as HTMLElement;
    expect(within(psi).getByText("계획 변경")).toBeInTheDocument(); // PSI는 롤오버 대상 밖
    expect(within(psi).queryByText("통보 기록 없음")).not.toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "통보 기록" }));
    fireEvent.change(within(dialog()).getByLabelText("상대 유형 (선택)"), { target: { value: "FORWARDER" } });
    expect(within(dialog()).getByRole("combobox", { name: "포워더 거래처" })).toBeInTheDocument();
    fireEvent.change(within(dialog()).getByLabelText("요지 (필수)"), { target: { value: "메일로 알림" } });
    fireEvent.click(within(dialog()).getByRole("button", { name: "통보 기록 저장" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(sent(stub.calls, `${SH}/milestone-changes/900/notices`, "POST")[0]?.body).toEqual({
      occurred_on: "2026-10-04",
      summary: "메일로 알림",
    });
    server.changes = [];
  });

  it("통보가 20건에 닿으면 '통보 기록' 버튼을 끄고 상한을 말한다", async () => {
    const notices = Array.from({ length: 20 }, (_, index) => ({
      comm_log_id: index + 1,
      occurred_on: "2026-10-04",
      summary: `통보 ${index + 1}`,
      partner_id: null,
      partner_name: null,
      actor_user_id: 1,
      created_at: "2026-10-04T03:00:00Z",
    }));
    server.changes = [milestoneChange({ notices })];
    open();
    await heading();
    const history = screen.getByRole("region", { name: "마일스톤 변경 이력" });
    expect(await within(history).findByText("통보 20")).toBeInTheDocument();
    expect(within(history).getByRole("button", { name: "통보 기록" })).toBeDisabled();
    expect(within(history).getByText("통보 기록은 변경 1건당 20건까지입니다.")).toBeInTheDocument();
    server.changes = [];
  });

  it("편집 권한이 없으면 이력은 보이되 '통보 기록' 버튼 0", async () => {
    server.changes = [milestoneChange()];
    open(detail({}, { allowed_actions: [] }), VIEWER);
    await heading();
    const history = screen.getByRole("region", { name: "마일스톤 변경 이력" });
    expect(await within(history).findByText("선사 스케줄 변경")).toBeInTheDocument();
    expect(within(history).queryByRole("button", { name: "통보 기록" })).not.toBeInTheDocument();
    server.changes = [];
  });
});
