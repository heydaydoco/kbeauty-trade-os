// 발주 상세 — OEM '생산 일정' 섹션(S3-2 PR-4b, 그룹 A·K): OEM 생산 발주에만(일반 구매 발주는 보드 요청 0) · 4행 순서 · 버튼 = 보드
// allowed_actions만 · 계획 본문(발주 경로) · 계획 변경 사유 빈칸 제출 불가 · 통보 선택지 없음 · 변경 이력(통보 열 없음) · 409 → 다시 불러오기.

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { stubGateFetch, type GateCall, type GateHandler } from "../test/gate-fixtures";
import { milestoneChange, oemBoard } from "../test/milestone-fixtures";
import { PO_LOG, poDetail } from "../test/po-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";
import { apiErrorResponse } from "../test/shipment-fixtures";
import type { OemMilestoneBoard } from "../lib/milestone";

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const PO = "/v1/purchase-orders/9";
const OEM_PO = poDetail({ po_kind: "OEM_PRODUCTION" });

function open(po = OEM_PO, board: OemMilestoneBoard = oemBoard({ po_id: 9 }), extra: GateHandler[] = [], me: unknown = TRADER) {
  return stubGateFetch(me, [
    ...extra,
    [`${PO}/status-log`, "GET", () => jsonResponse(PO_LOG)],
    ["/v1/users/lookup?size=200", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }]))],
    [`${PO}/milestones`, "GET", () => jsonResponse(board)],
    [`${PO}/milestone-changes`, "GET", () => jsonResponse(page([milestoneChange({ milestone_type: "FILLING", reason: "원료 지연" })]))],
    [PO, "GET", () => jsonResponse(po)],
  ]);
}

function render(po = OEM_PO, board?: OemMilestoneBoard, extra: GateHandler[] = [], me: unknown = TRADER) {
  const stub = open(po, board, extra, me);
  renderWithProviders(<AppRoutes />, { route: "/purchase-orders/9" });
  return stub;
}

const sent = (calls: GateCall[], path: string, method: string) => calls.filter((c) => c.method === method && c.url === `/api${path}`);
const card = (name: string) => screen.getByRole("listitem", { name });
const dialog = () => screen.getByRole("dialog");

describe("OEM 생산 일정 섹션", () => {
  it("OEM 생산 발주: 원료수급 → 충진 → 포장 → 출하검사 4행, 휴일·통관·롤오버 배지 0, 버튼은 보드 allowed_actions", async () => {
    render();
    const list = await screen.findByRole("list", { name: "생산 일정" });
    expect(within(list).getAllByRole("listitem").map((li) => li.getAttribute("aria-label"))).toEqual(["원료수급", "충진", "포장", "출하검사"]);
    expect(within(list).queryByText(/휴일|미수리|롤오버/)).not.toBeInTheDocument();
    expect(within(card("충진")).getByRole("button", { name: "계획 입력" })).toBeInTheDocument();
    expect(screen.getByText(/알림·휴일 경고 대상이 아닙니다/)).toBeInTheDocument();
  });

  it("일반 구매 발주에는 섹션이 없고 보드 요청도 0(서버 422 OWNER_NOT_OEM을 부르지 않는다)", async () => {
    const stub = render(poDetail({ po_kind: "PURCHASE" }));
    await screen.findByRole("heading", { name: /PO-2026-0001/ });
    expect(screen.queryByRole("heading", { name: "생산 일정" })).not.toBeInTheDocument();
    expect(sent(stub.calls, `${PO}/milestones`, "GET")).toHaveLength(0);
  });

  it("보드가 EDIT_MILESTONES를 주지 않으면(조회 역할·취소 발주) 버튼 0 + 서버 판정 안내", async () => {
    render(OEM_PO, oemBoard({ po_id: 9, allowed_actions: [] }), [], VIEWER);
    const list = await screen.findByRole("list", { name: "생산 일정" });
    expect(within(list).queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByText(/지금은 이 발주의 생산 일정을 기록할 수 없습니다/)).toBeInTheDocument();
  });

  it("계획 입력 → POST /purchase-orders/9/milestones/FILLING/plan {planned_on}, 응답 보드 반영", async () => {
    const after = oemBoard({ po_id: 9 }, { FILLING: { planned: "2026-10-20", effective: { value: "2026-10-20", basis: "PLANNED" }, days_left: 16, is_overdue: false, milestone_id: 81, version: 1 } });
    const stub = render(OEM_PO, undefined, [[`${PO}/milestones/FILLING/plan`, "POST", () => jsonResponse({ board: after, change: { id: 910, change_kind: "PLAN_SET" } })]]);
    await screen.findByRole("list", { name: "생산 일정" });
    fireEvent.click(within(card("충진")).getByRole("button", { name: "계획 입력" }));
    expect(within(dialog()).getByRole("heading", { name: "계획 입력 — 충진" })).toBeInTheDocument();
    fireEvent.change(within(dialog()).getByLabelText(/계획일/), { target: { value: "2026-10-20" } });
    fireEvent.click(within(dialog()).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(sent(stub.calls, `${PO}/milestones/FILLING/plan`, "POST")[0]?.body).toEqual({ planned_on: "2026-10-20" });
    expect(await within(card("충진")).findByText("D-16")).toBeInTheDocument();
  });

  it("계획 변경 = '계획 변경'(롤오버 아님) — 사유 빈칸 제출 불가, 통보 선택지 없음(통보 통로는 선적 전용)", async () => {
    const board = oemBoard({ po_id: 9 }, { PACKING: { planned: "2026-10-25", milestone_id: 82, version: 2 } });
    const stub = render(OEM_PO, board, [[`${PO}/milestones/PACKING/plan`, "POST", () => jsonResponse({ board, change: { id: 911, change_kind: "PLAN_CHANGED" } })]]);
    await screen.findByRole("list", { name: "생산 일정" });
    fireEvent.click(within(card("포장")).getByRole("button", { name: "계획 변경" }));
    const box = dialog();
    expect(within(box).getByRole("heading", { name: "계획 변경 — 포장" })).toBeInTheDocument();
    expect(within(box).queryByLabelText("통보도 지금 기록")).not.toBeInTheDocument();
    fireEvent.change(within(box).getByLabelText(/계획일/), { target: { value: "2026-10-28" } });
    expect(within(box).getByRole("button", { name: "저장" })).toBeDisabled();
    fireEvent.change(within(box).getByLabelText("변경 사유 (필수)"), { target: { value: "충진 지연" } });
    fireEvent.click(within(box).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(sent(stub.calls, `${PO}/milestones/PACKING/plan`, "POST")).toHaveLength(1));
    expect(sent(stub.calls, `${PO}/milestones/PACKING/plan`, "POST")[0]?.body).toEqual({ planned_on: "2026-10-28", version: 2, reason: "충진 지연" });
  });

  it("최초 계획 동시 입력 409 DUPLICATE_TYPE → 서버 문구 + '최신 내용 불러오기'(보드 재조회)", async () => {
    const stub = render(OEM_PO, undefined, [
      [`${PO}/milestones/FILLING/plan`, "POST", () => jsonResponse(apiErrorResponse("SHIPMENTS.MILESTONE.DUPLICATE_TYPE", "이미 이 종류의 생산 일정이 있습니다."), 409)],
    ]);
    await screen.findByRole("list", { name: "생산 일정" });
    const before = sent(stub.calls, `${PO}/milestones`, "GET").length;
    fireEvent.click(within(card("충진")).getByRole("button", { name: "계획 입력" }));
    fireEvent.change(within(dialog()).getByLabelText(/계획일/), { target: { value: "2026-10-20" } });
    fireEvent.click(within(dialog()).getByRole("button", { name: "저장" }));
    expect(await within(dialog()).findByText("이미 이 종류의 생산 일정이 있습니다.")).toBeInTheDocument();
    fireEvent.click(within(dialog()).getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(sent(stub.calls, `${PO}/milestones`, "GET").length).toBeGreaterThan(before));
  });

  it("변경 이력 — '계획 변경'·사유, 통보 열·버튼 없음", async () => {
    render();
    const history = await screen.findByRole("region", { name: "생산 일정 변경 이력" });
    const row = (await within(history).findByText("원료 지연")).closest("tr") as HTMLElement;
    expect(within(row).getByText("충진")).toBeInTheDocument();
    expect(within(row).getByText("계획 변경")).toBeInTheDocument();
    expect(within(history).queryByRole("columnheader", { name: "통보" })).not.toBeInTheDocument();
    expect(within(history).queryByRole("button", { name: "통보 기록" })).not.toBeInTheDocument();
  });
});

describe("OEM 생산 일정 — 적대 검토 반영", () => {
  it("med ① 409 OWNER_NOT_ACTIVE(그 사이 발주 취소) → '최신 내용 불러오기' → 보드 재조회 뒤 버튼 0", async () => {
    let board = oemBoard({ po_id: 9 });
    const stub = stubGateFetch(TRADER, [
      [
        `${PO}/milestones/FILLING/plan`,
        "POST",
        () => {
          board = oemBoard({ po_id: 9, allowed_actions: [] });
          return jsonResponse(apiErrorResponse("SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE", "취소된 발주입니다."), 409);
        },
      ],
      [`${PO}/status-log`, "GET", () => jsonResponse(PO_LOG)],
      ["/v1/users/lookup?size=200", "GET", () => jsonResponse(page([]))],
      [`${PO}/milestones`, "GET", () => jsonResponse(board)],
      [`${PO}/milestone-changes`, "GET", () => jsonResponse(page([]))],
      [PO, "GET", () => jsonResponse(OEM_PO)],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/purchase-orders/9" });
    await screen.findByRole("list", { name: "생산 일정" });
    fireEvent.click(within(card("충진")).getByRole("button", { name: "계획 입력" }));
    fireEvent.change(within(dialog()).getByLabelText(/계획일/), { target: { value: "2026-10-20" } });
    fireEvent.click(within(dialog()).getByRole("button", { name: "저장" }));
    fireEvent.click(await within(dialog()).findByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(within(screen.getByRole("list", { name: "생산 일정" })).queryAllByRole("button")).toHaveLength(0));
    expect(sent(stub.calls, `${PO}/milestones`, "GET").length).toBeGreaterThan(1);
  });

  it("med ③ 사유 칸 아래 원가 금지 안내(ADR-0057) — aria-describedby로 연결", async () => {
    render(OEM_PO, oemBoard({ po_id: 9 }, { PACKING: { planned: "2026-10-25", milestone_id: 82, version: 2 } }));
    await screen.findByRole("list", { name: "생산 일정" });
    fireEvent.click(within(card("포장")).getByRole("button", { name: "계획 변경" }));
    const reason = within(dialog()).getByLabelText("변경 사유 (필수)");
    const hint = document.getElementById(reason.getAttribute("aria-describedby") ?? "");
    expect(hint).toHaveTextContent("원가(단가·금액)를 적지 마세요 — 원가 열람 권한이 없는 사용자에게도 보입니다.");
  });
});
