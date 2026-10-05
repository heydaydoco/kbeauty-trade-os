// 수입선적 상세 (S3-2 PR-5b — 그룹 A·G·K) — 금액·통화 칸 0(원가 열람 역할도)·원천 발주 라인/배정 가능량·'선적 확정' 문구(R-5a-8)·
// 라인 수량 수정 409 EXCEEDS_ASSIGNABLE 칸별 + 발주 재조회·라인 추가 후보 = 발주 라인(배정 가능량)·문서 흐름 미호출.
// 응답은 백엔드 ImportShipmentDetail 그대로(금액·통화 키 자체가 없다). fetch 스텁은 정확 URL·메서드 일치(stubGateFetch).

import { QueryClient } from "@tanstack/react-query";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { purchaseOrderDetailKey } from "../lib/purchase-order";
import type { ImportShipmentDetail } from "../lib/shipment";
import { stubGateFetch, type GateCall, type GateHandler } from "../test/gate-fixtures";
import { exportBoard } from "../test/milestone-fixtures";
import { PO_LINE, SENTINELS, poDetail } from "../test/po-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";
import { AUTO_SHIPPER, IMPORT_LINE, apiErrorResponse, importShipmentDetail } from "../test/shipment-fixtures";

const LOGISTICS = { ...TRADER, id: 5, display_name: "물류 담당", roles: ["LOGISTICS"] };

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const SH = "/v1/shipments/41";
const PO = "/v1/purchase-orders/4";
const LOG = {
  items: [
    { id: 1, occurred_at: "2026-10-04T01:00:00Z", from_status: "PLANNED", to_status: "RELEASE_ORDERED", reason: null, actor_user_id: 5, actor_name: "물류 담당", automatic: false },
  ],
  total: 1,
  page: 1,
  size: 50,
};

/** 원천 발주 — 라인 77(이 선적에 있음)·78(배정 가능 30). 원가 키는 Full 응답 그대로(화면이 후보로 옮기지 않는지 함께 본다). */
const sourcePo = (assignable78 = 30) =>
  poDetail({
    id: 4,
    doc_number: "PO-2026-0004",
    lines: [
      { ...PO_LINE, id: 77, line_no: 1, quantity: 100, assignable_quantity: 40 },
      { ...PO_LINE, id: 78, line_no: 2, sku_code: "SKU-002", sku_name_ko: "토너", quantity: 30, assignable_quantity: assignable78 },
    ],
  });

const server: { detail: ImportShipmentDetail; po: ReturnType<typeof sourcePo> } = { detail: importShipmentDetail(), po: sourcePo() };
const writes = (next: ImportShipmentDetail, status = 200) => () => {
  server.detail = next;
  return jsonResponse(next, status);
};

function open(detail = importShipmentDetail(), me: unknown = TRADER, extra: GateHandler[] = [], client?: QueryClient) {
  server.detail = detail;
  server.po = sourcePo();
  const stub = stubGateFetch(me, [
    ...extra,
    [`${SH}/status-log`, "GET", () => jsonResponse(LOG)],
    ["/v1/users/lookup?size=200", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }]))],
    [PO, "GET", () => jsonResponse(server.po)],
    [SH, "GET", () => jsonResponse(server.detail)],
  ]);
  renderWithProviders(<AppRoutes />, { route: "/shipments/41", client });
  return stub;
}

const heading = () => screen.findByRole("heading", { name: /SH-2026-0011/ });
const sent = (calls: GateCall[], path: string, method: string) => calls.filter((c) => c.method === method && c.url === `/api${path}`);

describe("수입선적 상세 — 금액·통화 칸 0(R-5a-4)", () => {
  it.each([
    ["무역(원가 열람 역할)", TRADER],
    ["조회", VIEWER],
  ])("%s: 선적 정보에 통화·고정 환율·합계 칸이 없고 원가 비복사 안내만 — 라인 표에도 단가·금액 열이 없다", async (_name, me) => {
    open(importShipmentDetail({ allowed_actions: [] }), me);
    await heading();
    const info = screen.getByRole("region", { name: "선적 정보" });
    const labels = Array.from(info.querySelectorAll("dt")).map((dt) => dt.textContent);
    expect(labels).not.toContain("통화");
    expect(labels).not.toContain("고정 환율");
    expect(labels).not.toContain("합계");
    expect(within(info).queryByText(/\(기준일/)).toBeNull(); // 과도기 " (기준일 —)" 표시 0
    expect(within(info).getByText(/수입선적은 발주의 단가·금액·통화·환율을 복사하지 않습니다/)).toBeInTheDocument();
    expect(within(info).getByRole("link", { name: "PO-2026-0004" })).toHaveAttribute("href", "/purchase-orders/4");
    expect(within(info).getByText("(발행)")).toBeInTheDocument(); // 발주 상태는 한국어 라벨(원문 ISSUED 아님)

    const lines = screen.getByRole("region", { name: "라인" });
    const headers = within(lines).getAllByRole("columnheader").map((th) => th.textContent);
    expect(headers).toEqual(["번호", "SKU", "품명", "수량", "원천 라인", "배정 가능", "DG", "가용재고"]);
    const row = within(lines).getByText("SKU-001").closest("tr") as HTMLElement;
    const cells = within(row).getAllByRole("cell").map((cell) => cell.textContent);
    expect(cells[4]).toBe("#1 · 발주 100");
    expect(cells[5]).toBe("40"); // 배정 가능량(서버 파생값)
    expect(lines.querySelector("tfoot")).toBeNull();
    for (const text of [info.textContent ?? "", lines.textContent ?? ""]) expect(text).not.toMatch(/USD|0\.00/);
  });

  it("문서 흐름은 부르지 않는다(판매 사슬 밖 — 원천 발주 링크만)", async () => {
    const { calls } = open(importShipmentDetail({ allowed_actions: [] }));
    await heading();
    expect(screen.getByText(/수입선적은 문서 흐름/)).toBeInTheDocument();
    expect(calls.filter((c) => c.url.includes("/document-flow/"))).toHaveLength(0);
  });
});

describe("수입선적 상세 — '선적 확정' 문구(R-5a-8 — 화면 문구만, 경로 무변경)", () => {
  it("버튼·대화상자·상태 배지·동결 시각·상태 이력이 '선적 확정'이고 '출고지시'는 없다 → 같은 release-order 경로", async () => {
    const released = importShipmentDetail({
      status: "RELEASE_ORDERED",
      version: 2,
      frozen_at: "2026-10-04T03:00:00Z",
      allowed_actions: ["CANCEL", "EDIT_META", "EDIT_PARTIES"],
    });
    const { calls } = open(importShipmentDetail(), LOGISTICS, [[`${SH}/release-order`, "POST", writes(released)]]);
    await heading();
    expect(screen.queryByRole("button", { name: "출고지시" })).toBeNull();
    expect(screen.getByText(/선적 확정 뒤에는 바꿀 수 없습니다/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "선적 확정" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "선적을 확정할까요?" })).toBeInTheDocument();
    expect(within(dialog).getByText(/공급사에 출하를 지시한 수입선적으로 확정합니다/)).toBeInTheDocument();
    expect(dialog.textContent).not.toContain("출고지시");
    fireEvent.click(within(dialog).getByRole("button", { name: "선적 확정" }));
    await waitFor(() => expect(sent(calls, `${SH}/release-order`, "POST")).toHaveLength(1));
    expect(sent(calls, `${SH}/release-order`, "POST")[0]!.body).toEqual({ version: 1 });
    expect(await screen.findByText(/선적 확정된 수입선적입니다/)).toBeInTheDocument();
    const info = screen.getByRole("region", { name: "선적 정보" });
    expect(within(info).getByText("선적 확정 시각")).toBeInTheDocument();
    expect(screen.getAllByText("선적 확정").length).toBeGreaterThan(0); // 상태 배지
    expect(await screen.findByText(/계획 → 선적 확정|선적 확정 \(/)).toBeInTheDocument(); // 상태 이력 라벨
    expect(document.body.textContent).not.toContain("출고지시");
  });

  it("취소 대화상자는 발주 라인 배정 가능량으로 돌아간다고 안내한다(수주 잔량 문구 0)", async () => {
    open(importShipmentDetail());
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("원천 발주 라인의 배정 가능량으로 돌아갑니다");
    expect(dialog.textContent).not.toContain("수주");
  });
});

describe("수입선적 상세 — 라인 수량 수정·추가(배정 가능량)", () => {
  it("수정 상한 안내 = 배정 가능 + 현재, 409 EXCEEDS_ASSIGNABLE이면 칸 아래 이 라인 최대 수량 + 발주 상세 재조회", async () => {
    const { calls } = open(importShipmentDetail(), TRADER, [
      [
        `${SH}/lines/601`,
        "PATCH",
        () =>
          jsonResponse(
            apiErrorResponse("SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE", "배정 가능량을 넘었습니다.", { assignable_quantity: { "77": 70 } }),
            409,
          ),
      ],
    ]);
    await heading();
    const lines = screen.getByRole("region", { name: "라인" });
    fireEvent.click(within(lines).getByRole("button", { name: "수정" }));
    const form = screen.getByRole("form", { name: "라인 1 수정" });
    expect(within(form).getByText("상한 = 배정 가능 40 + 현재 60")).toBeInTheDocument();
    const poReads = sent(calls, PO, "GET").length;
    fireEvent.change(within(form).getByRole("textbox"), { target: { value: "90" } });
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));
    expect(await screen.findByText(/— 이 라인 최대 수량 70/)).toBeInTheDocument();
    expect(sent(calls, `${SH}/lines/601`, "PATCH")[0]!.body).toEqual({ version: 1, quantity: 90 });
    await waitFor(() => expect(sent(calls, PO, "GET").length).toBeGreaterThan(poReads));
  });

  it("라인 추가: 후보 = 이 선적에 없는 발주 라인(배정 가능량 표기) → [배정 가능 전부] → POST {version, source_line_id: po_line_id, quantity}", async () => {
    const added = importShipmentDetail({
      version: 2,
      lines: [IMPORT_LINE, { ...IMPORT_LINE, id: 602, line_no: 2, po_line_id: 78, quantity: 30, source_line: { id: 78, line_no: 2, quantity: 30, remaining_after: 0 } }],
    });
    const { calls } = open(importShipmentDetail(), TRADER, [[`${SH}/lines`, "POST", writes(added, 201)]]);
    await heading();
    const form = await screen.findByRole("form", { name: "라인 추가" });
    expect(within(form).getByText(/원천 발주 PO-2026-0004의 라인 중/)).toBeInTheDocument();
    const select = await within(form).findByRole("combobox");
    await within(select).findByRole("option", { name: "#2 SKU-002 토너 — 배정 가능 30" });
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["선택", "#2 SKU-002 토너 — 배정 가능 30"]);
    for (const sentinel of SENTINELS) expect(document.body.innerHTML).not.toContain(sentinel); // 발주 원가 값을 후보·화면으로 옮기지 않는다
    fireEvent.change(select, { target: { value: "78" } });
    fireEvent.click(within(form).getByRole("button", { name: "배정 가능 전부" }));
    fireEvent.click(within(form).getByRole("button", { name: "라인 추가" }));
    await waitFor(() => expect(sent(calls, `${SH}/lines`, "POST")).toHaveLength(1));
    const post = sent(calls, `${SH}/lines`, "POST")[0]!;
    expect(post.body).toEqual({ version: 1, source_line_id: 78, quantity: 30 });
    expect(post.headers["Idempotency-Key"]).toMatch(/^key-/);
  });

  it("라인 추가 409 EXCEEDS_ASSIGNABLE 뒤 발주를 다시 받아 드롭다운의 배정 가능량이 서버 값으로 바뀐다", async () => {
    open(importShipmentDetail(), TRADER, [
      [
        `${SH}/lines`,
        "POST",
        () => {
          server.po = sourcePo(12);
          return jsonResponse(
            apiErrorResponse("SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE", "배정 가능량을 넘었습니다.", { assignable_quantity: { "78": 12 } }),
            409,
          );
        },
      ],
    ]);
    await heading();
    const form = await screen.findByRole("form", { name: "라인 추가" });
    const select = await within(form).findByRole("combobox");
    await within(select).findByRole("option", { name: "#2 SKU-002 토너 — 배정 가능 30" });
    fireEvent.change(select, { target: { value: "78" } });
    fireEvent.change(within(form).getByRole("textbox"), { target: { value: "30" } });
    fireEvent.click(within(form).getByRole("button", { name: "라인 추가" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("— 남은 배정 가능량 12");
    expect(await within(select).findByRole("option", { name: "#2 SKU-002 토너 — 배정 가능 12" })).toBeInTheDocument();
  });

  it("당사자 안내: 송하인 = 발주 공급사 자동, 수하인 = 자사(수주 바이어 문구 0)", async () => {
    open(importShipmentDetail({ parties: [AUTO_SHIPPER] }));
    await heading();
    const section = screen.getByRole("region", { name: "당사자" });
    expect(section.textContent).toContain("송하인은 발주 공급사에서 자동으로 복사되고, 수하인은 자사입니다.");
    expect(section.textContent).not.toContain("수주 바이어");
    const row = within(section).getByText("Seoul Cosmetics Co., Ltd.").closest("tr") as HTMLElement;
    expect(within(row).getByText("송하인")).toBeInTheDocument();
    expect(within(row).getByText("원천에서 복사")).toBeInTheDocument();
  });
});

// ── 적대 검토 반영(low ②③④) — 마일스톤: '선적 확정' 안내·422 문구 대체·원천 발주 입고예정 캐시 무효화 ──

const MILESTONE_ACTIONS = ["RELEASE_ORDER", "CANCEL", "EDIT_LINES", "EDIT_COUNTRIES", "EDIT_META", "EDIT_PARTIES", "EDIT_MILESTONES", "PLAN_DRAFT"];
const withMilestones = (over: Partial<ImportShipmentDetail> = {}) =>
  importShipmentDetail({ allowed_actions: MILESTONE_ACTIONS, milestones: exportBoard(), ...over });
const card = (name: string) => within(screen.getByRole("list", { name: "마일스톤" })).getByRole("listitem", { name });

describe("수입선적 마일스톤 — '선적 확정' 문구·입고예정 캐시(적대 검토 반영)", () => {
  it.each(["ETD", "ETA"])("%s 실적 대화상자 안내는 '선적 확정 뒤에만 기록합니다'('출고지시' 0)", async (type) => {
    open(withMilestones());
    await heading();
    fireEvent.click(within(card(type)).getByRole("button", { name: "실적 입력" }));
    const box = screen.getByRole("dialog");
    expect(box.textContent).toContain("ETD·B/L 발행·ETA 실적은 선적 확정 뒤에만 기록합니다.");
    expect(box.textContent).not.toContain("출고지시");
  });

  it("서버 422 ACTUAL_BEFORE_RELEASE의 '출고지시' 문구를 '선적 확정'으로 바꿔 보인다", async () => {
    open(withMilestones(), TRADER, [
      [
        `${SH}/milestones/ETA/actual`,
        "POST",
        () =>
          jsonResponse(
            apiErrorResponse(
              "SHIPMENTS.MILESTONE.ACTUAL_BEFORE_RELEASE",
              "ETD·B/L 발행·ETA 실적은 출고지시 뒤에만 기록할 수 있습니다. 출고지시를 먼저 진행해 주세요.",
            ),
            422,
          ),
      ],
    ]);
    await heading();
    fireEvent.click(within(card("ETA")).getByRole("button", { name: "실적 입력" }));
    const box = screen.getByRole("dialog");
    fireEvent.change(within(box).getByLabelText(/실적일/), { target: { value: "2026-10-03" } });
    fireEvent.click(within(box).getByRole("button", { name: "저장" }));
    expect(
      await within(box).findByText("ETD·B/L 발행·ETA 실적은 선적 확정 뒤에만 기록할 수 있습니다. 선적 확정을 먼저 진행해 주세요."),
    ).toBeInTheDocument();
    expect(box.textContent).not.toContain("출고지시");
  });

  it("ETA 계획 저장 뒤 원천 발주 상세 캐시(입고예정)를 exact로 무효화한다 — 다른 발주는 그대로", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const spy = vi.spyOn(client, "invalidateQueries");
    const poInvalidations = () =>
      spy.mock.calls.filter(([filters]) => JSON.stringify(filters?.queryKey) === JSON.stringify(purchaseOrderDetailKey(4)) && filters?.exact === true);
    const after = withMilestones({ milestones: exportBoard({ ETA: { planned: "2026-11-02", effective: { value: "2026-11-02", basis: "PLANNED" }, milestone_id: 70, version: 1 } }) });
    open(withMilestones(), TRADER, [[`${SH}/milestones/ETA/plan`, "POST", () => jsonResponse({ board: after.milestones, change: { id: 1, change_kind: "PLAN_SET" } })]], client);
    await heading();
    expect(poInvalidations()).toHaveLength(0);
    fireEvent.click(within(card("ETA")).getByRole("button", { name: "계획 입력" }));
    const box = screen.getByRole("dialog");
    fireEvent.change(within(box).getByLabelText(/계획일/), { target: { value: "2026-11-02" } });
    fireEvent.click(within(box).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(poInvalidations().length).toBeGreaterThan(0));
    expect(spy.mock.calls.some(([filters]) => JSON.stringify(filters?.queryKey) === JSON.stringify(purchaseOrderDetailKey(5)))).toBe(false);
  });
});
