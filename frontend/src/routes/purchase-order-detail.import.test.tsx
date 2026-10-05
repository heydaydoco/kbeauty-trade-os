// 발주 상세 — 수입선적 (S3-2 PR-5b — 그룹 A·G·J·K / PROGRESS 'S3-2 PR-5a' 인계 계약)
// 라인 '수입선적 배정 가능'·'입고예정'(NONE/UNSCHEDULED/SCHEDULED·미배정 병기·재생 본문 '정보 없음')·'수입선적 만들기' 노출 조건·
// 2단 대화상자(본문 po_line_id·금액 칸 0·409 EXCEEDS_ASSIGNABLE 칸별 + 재조회 + 0 자동 비움·422 칸별·결과 불명 같은 키·소비 불가 409)·
// 이 발주의 수입선적 목록(금액 열 없음·'선적 확정' 배지)·취소 409 SUCCESSOR_ALIVE 링크.

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import type { ExpectedReceipt, PoLine } from "../lib/purchase-order";
import { PO_LINE, PO_LINE_HIDDEN, PO_LOG, SENTINELS, poDetail, poDetailHidden } from "../test/po-fixtures";
import { stubFetch, type Call } from "../test/qt-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";
import { apiErrorResponse, importShipmentDetail, importShipmentListItem, importShipmentPreview } from "../test/shipment-fixtures";

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

const receipt = (over: Partial<ExpectedReceipt> = {}): ExpectedReceipt => ({
  status: "NONE",
  value: null,
  basis: null,
  shipment_count: 0,
  unscheduled_count: 0,
  ...over,
});

/** 라인 2개 — 41(배정 가능 10)·42(배정 가능 5). */
const LINES: PoLine[] = [
  { ...PO_LINE, id: 41, line_no: 1, quantity: 10, assignable_quantity: 10 },
  { ...PO_LINE, id: 42, line_no: 2, sku_code: "SKU-002", sku_name_ko: "토너", quantity: 8, assignable_quantity: 5 },
];

const PREVIEW = "/v1/purchase-orders/9/shipments/preview";
const CREATE = "/v1/purchase-orders/9/shipments";

const server = { po: poDetail({ lines: LINES }) };

function open(po = poDetail({ lines: LINES }), extra: Array<[string, string, () => Response]> = [], me: unknown = TRADER) {
  server.po = po;
  const stub = stubFetch(me, [
    // ★ includes 일치라 더 긴 경로(미리보기)를 먼저 둔다.
    ...extra,
    ["/v1/purchase-orders/9/status-log", "GET", () => jsonResponse(PO_LOG)],
    ["/v1/users/lookup", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }]))],
    ["/v1/purchase-orders/9", "GET", () => jsonResponse(server.po)],
  ]);
  renderWithProviders(<AppRoutes />, { route: "/purchase-orders/9" });
  return stub;
}

const heading = () => screen.findByRole("heading", { name: /PO-2026-0001/ });
const posts = (calls: Call[], path: string) => calls.filter((c) => c.method === "POST" && c.url === `/api${path}`);
const lineRow = (sku: string) => within(screen.getByRole("region", { name: "라인" })).getByText(sku).closest("tr") as HTMLElement;
const cellUnder = (row: HTMLElement, header: string) => {
  const headers = within(screen.getByRole("region", { name: "라인" }))
    .getAllByRole("columnheader")
    .map((th) => th.textContent);
  return within(row).getAllByRole("cell")[headers.indexOf(header)] as HTMLElement;
};

async function openDialog() {
  fireEvent.click(await screen.findByRole("button", { name: "수입선적 만들기" }));
  return screen.getByRole("dialog");
}

function fillStep1(dialog: HTMLElement, quantities: Record<number, string>) {
  for (const [lineNo, value] of Object.entries(quantities)) {
    fireEvent.change(within(dialog).getByLabelText(`라인 ${lineNo} 이번 선적 수량`), { target: { value } });
  }
  fireEvent.change(within(dialog).getByLabelText("출발국 (필수)"), { target: { value: "CN" } });
  fireEvent.change(within(dialog).getByLabelText("도착국 (필수)"), { target: { value: "KR" } });
}

describe("발주 상세 라인 — 배정 가능량·입고예정(원가 무관 파생값)", () => {
  it("열 머리·NONE·미배정 병기 — 숫자 가운데(num)·nowrap", async () => {
    open();
    await heading();
    const region = screen.getByRole("region", { name: "라인" });
    const headers = within(region).getAllByRole("columnheader").map((th) => th.textContent);
    expect(headers).toContain("수입선적 배정 가능");
    expect(headers).toContain("입고예정");
    const row = lineRow("SKU-001");
    expect(cellUnder(row, "수입선적 배정 가능")).toHaveTextContent(/^10$/);
    expect(cellUnder(row, "수입선적 배정 가능").className).toContain("num");
    expect(cellUnder(row, "입고예정")).toHaveTextContent("입고예정 미정(수입선적 없음)");
    expect(cellUnder(row, "입고예정")).toHaveTextContent("미배정 10");
    // 합계 줄은 새 열만큼 밀린다(colSpan 9 + 금액 1 = 10열).
    expect(region.querySelector("tfoot td")?.getAttribute("colspan")).toBe("9");
  });

  it("UNSCHEDULED 'ETA 미정 n건' / SCHEDULED 날짜 문자열 그대로 + 예정·실적 / 배정 가능 0이면 미배정 병기 없음", async () => {
    open(
      poDetail({
        lines: [
          { ...LINES[0]!, assignable_quantity: 4, expected_receipt: receipt({ status: "UNSCHEDULED", shipment_count: 3, unscheduled_count: 2 }) },
          {
            ...LINES[1]!,
            assignable_quantity: 0,
            expected_receipt: receipt({ status: "SCHEDULED", value: "2026-11-02", basis: "PLANNED", shipment_count: 1 }),
          },
          {
            ...LINES[1]!,
            id: 43,
            line_no: 3,
            sku_code: "SKU-003",
            assignable_quantity: 0,
            expected_receipt: receipt({ status: "SCHEDULED", value: "2026-10-30", basis: "ACTUAL", shipment_count: 2 }),
          },
        ],
      }),
    );
    await heading();
    expect(cellUnder(lineRow("SKU-001"), "입고예정")).toHaveTextContent("ETA 미정 2건");
    expect(cellUnder(lineRow("SKU-001"), "입고예정")).toHaveTextContent("미배정 4");
    const planned = cellUnder(lineRow("SKU-002"), "입고예정");
    expect(planned).toHaveTextContent("2026-11-02 예정");
    expect(planned.textContent).not.toContain("미배정");
    expect(cellUnder(lineRow("SKU-003"), "입고예정")).toHaveTextContent("2026-10-30 실적");
  });

  it("조회 역할(CostHidden)도 같은 배정 가능량·입고예정을 본다 — 원가는 0", async () => {
    open(
      poDetailHidden({
        lines: [{ ...PO_LINE_HIDDEN, assignable_quantity: 7, expected_receipt: receipt({ status: "SCHEDULED", value: "2026-11-02", basis: "PLANNED", shipment_count: 1 }) }],
      }),
      [],
      VIEWER,
    );
    await heading();
    expect(cellUnder(lineRow("SKU-001"), "수입선적 배정 가능")).toHaveTextContent(/^7$/);
    expect(cellUnder(lineRow("SKU-001"), "입고예정")).toHaveTextContent("2026-11-02 예정");
    for (const sentinel of SENTINELS) expect(document.body.innerHTML).not.toContain(sentinel);
    expect(screen.queryByRole("button", { name: "수입선적 만들기" })).toBeNull();
  });

  it("재생 본문(두 필드 null — R-5a-6)은 '정보 없음 — 새로고침' 안내, 0으로 그리지 않고 만들기도 열지 않는다", async () => {
    const { calls } = open(poDetail({ lines: [{ ...PO_LINE, assignable_quantity: null, expected_receipt: null }] }));
    await heading();
    expect(screen.getByText(/배정 가능량·입고예정 정보가 없습니다/)).toBeInTheDocument();
    expect(cellUnder(lineRow("SKU-001"), "수입선적 배정 가능")).toHaveTextContent("정보 없음");
    expect(cellUnder(lineRow("SKU-001"), "입고예정")).toHaveTextContent("정보 없음");
    expect(screen.queryByRole("button", { name: "수입선적 만들기" })).toBeNull();
    const reads = calls.filter((c) => c.url === "/api/v1/purchase-orders/9").length;
    const banner = screen.getByText(/배정 가능량·입고예정 정보가 없습니다/).closest("div") as HTMLElement;
    fireEvent.click(within(banner).getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/v1/purchase-orders/9").length).toBeGreaterThan(reads));
  });
});

describe("'수입선적 만들기' 노출 조건(서버 재검사 — 무역·관리자 + 발행·공급사 확인 + 배정 가능량 합 > 0)", () => {
  it.each([
    ["무역 + 발행", TRADER, "ISSUED", LINES, true],
    ["무역 + 공급사 확인", TRADER, "SUPPLIER_CONFIRMED", LINES, true],
    ["관리자 + 발행", { ...TRADER, roles: ["ADMIN"] }, "ISSUED", LINES, true],
    ["무역 + 취소", TRADER, "CANCELLED", LINES, false],
    ["무역 + 배정 가능 0", TRADER, "ISSUED", LINES.map((line) => ({ ...line, assignable_quantity: 0 })), false],
    ["물류", LOGISTICS, "ISSUED", LINES, false],
    ["조회", VIEWER, "ISSUED", LINES, false],
  ] as const)("%s → %s", async (_name, me, status, lines, shown) => {
    open(poDetail({ status, lines: [...lines] }), [], me);
    await heading();
    expect(await screen.findByRole("heading", { name: "수입선적" })).toBeInTheDocument();
    if (shown) expect(screen.getByRole("button", { name: "수입선적 만들기" })).toBeInTheDocument();
    else expect(screen.queryByRole("button", { name: "수입선적 만들기" })).toBeNull();
  });

  it("배정 가능 0은 이유를 안내한다(막다른 길 0)", async () => {
    open(poDetail({ lines: LINES.map((line) => ({ ...line, assignable_quantity: 0 })) }));
    await heading();
    expect(screen.getByText(/배정 가능량이 없습니다 — 모든 수량이 수입선적에 배정되었습니다/)).toBeInTheDocument();
  });
});

describe("수입선적 만들기 2단 대화상자", () => {
  it("① 입력(빈칸 기본·발주/배정 가능 표기) → ② 미리보기(금액·통화 칸 0) → 생성 확정(키 1개) → 선적 상세로", async () => {
    const { calls } = open(poDetail({ lines: LINES }), [
      [PREVIEW, "POST", () => jsonResponse(importShipmentPreview({ po_id: 9, po_doc_number: "PO-2026-0001" }))],
      [CREATE, "POST", () => jsonResponse(importShipmentDetail(), 201)],
      ["/v1/shipments/41/", "GET", () => jsonResponse(page([]))], // 상세의 하위 목록(통관·이력 등) — 상세 본문과 구분
      ["/v1/shipments/41", "GET", () => jsonResponse(importShipmentDetail())],
    ]);
    await heading();
    const dialog = await openDialog();
    expect(within(dialog).getByRole("heading", { name: "수입선적 만들기 — PO-2026-0001" })).toBeInTheDocument();
    expect(dialog.textContent).toContain("단가·금액·통화·환율은 복사하지 않습니다");
    expect(dialog.textContent).toContain("송하인은 발주 공급사의 영문 이름·주소가 자동으로 복사되고, 수하인은 자사입니다");
    const first = within(dialog).getByLabelText("라인 1 이번 선적 수량");
    expect(first).toHaveValue(""); // 기본값 빈칸(전량 선적 실수 방지)
    const item = first.closest("li") as HTMLElement;
    expect(item.textContent).toContain("발주 10");
    expect(item.textContent).toContain("배정 가능 10");
    for (const sentinel of SENTINELS) expect(dialog.innerHTML).not.toContain(sentinel); // 발주 원가를 대화상자로 옮기지 않는다

    fillStep1(dialog, { 1: "6" });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    await waitFor(() => expect(posts(calls, PREVIEW)).toHaveLength(1));
    expect(posts(calls, PREVIEW)[0]!.body).toEqual({ lines: [{ po_line_id: 41, quantity: 6 }], origin_country_code: "CN", dest_country_code: "KR" });

    expect(await within(dialog).findByRole("heading", { name: "수입선적을 만들까요? — PO-2026-0001" })).toBeInTheDocument();
    const headers = within(dialog).getAllByRole("columnheader").map((th) => th.textContent);
    expect(headers).toEqual(["번호", "SKU", "품명", "이번 수량", "배정 전 배정 가능", "배정 후 배정 가능", "DG"]);
    const labels = Array.from(dialog.querySelectorAll("dt")).map((dt) => dt.textContent);
    expect(labels).toEqual(["공급사", "출발국 → 도착국", "증빙일", "결제조건", "인코텀즈"]); // 통화·고정 환율 칸 0
    expect(dialog.querySelector("tfoot")).toBeNull(); // 합계 줄 0
    expect(dialog.textContent).not.toMatch(/USD|기준일|합계 \(서버 계산\)/);
    expect(within(dialog).getByText("(자동 — 발주 공급사)")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "생성 확정" }));
    fireEvent.click(within(dialog).getByRole("button", { name: /생성 확정|만드는 중/ })); // 더블클릭 — 동기 잠금
    expect(await screen.findByRole("heading", { name: /SH-2026-0011/ })).toBeInTheDocument();
    expect(posts(calls, CREATE)).toHaveLength(1);
    expect(posts(calls, CREATE)[0]!.body).toEqual(posts(calls, PREVIEW)[0]!.body);
    expect(posts(calls, CREATE)[0]!.headers["Idempotency-Key"]).toMatch(/^key-/);
  });

  it("미리보기 409 EXCEEDS_ASSIGNABLE — 칸 아래 '배정 가능량 n 초과'·발주 재조회, 재조회로 0이 된 라인은 자동으로 비운다", async () => {
    let conflicted = false;
    const { calls } = open(poDetail({ lines: LINES }), [
      [
        PREVIEW,
        "POST",
        () => {
          conflicted = true;
          server.po = poDetail({ lines: [{ ...LINES[0]!, assignable_quantity: 3 }, { ...LINES[1]!, assignable_quantity: 0 }] });
          return jsonResponse(
            apiErrorResponse("SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE", "요청 수량이 발주 라인의 배정 가능량을 넘었습니다.", {
              assignable_quantity: { "41": 3, "42": 0 },
            }),
            409,
          );
        },
      ],
    ]);
    await heading();
    const dialog = await openDialog();
    fillStep1(dialog, { 1: "8", 2: "5" });
    const reads = calls.filter((c) => c.url === "/api/v1/purchase-orders/9").length;
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    expect(await within(dialog).findByText("서버 확인: 배정 가능량 3 초과 — 수량을 3 이하로 고쳐 주세요.")).toBeInTheDocument();
    expect(within(dialog).getByText("라인별 배정 가능량은 각 수량 칸 아래에 표시했습니다.")).toBeInTheDocument();
    expect(conflicted).toBe(true);
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/v1/purchase-orders/9").length).toBeGreaterThan(reads));
    // 라인 2는 재조회 뒤 배정 가능 0 → 수량을 비우고 알린다(막다른 길 0). 라인 1 입력은 그대로.
    expect(await within(dialog).findByText("배정 가능량이 없어 이번 선적에서 뺐습니다(서버 재확인 결과).")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("라인 2 이번 선적 수량")).toHaveValue("");
    expect(within(dialog).getByLabelText("라인 2 이번 선적 수량")).toBeDisabled();
    expect(within(dialog).getByLabelText("라인 1 이번 선적 수량")).toHaveValue("8");
    // PO 잔량 오류 문구(EXCEEDS_OPEN 칸 안내)와 섞이지 않는다.
    expect(dialog.textContent).not.toContain("남은 수량");
  });

  it("생성 확정 409 EXCEEDS_ASSIGNABLE — 1단으로 돌아가 칸별 안내, 고친 본문은 새 키", async () => {
    let creates = 0;
    const { calls } = open(poDetail({ lines: LINES }), [
      [PREVIEW, "POST", () => jsonResponse(importShipmentPreview({ po_id: 9, po_doc_number: "PO-2026-0001" }))],
      [
        CREATE,
        "POST",
        () => {
          creates += 1;
          return jsonResponse(
            apiErrorResponse("SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE", "배정 가능량 초과", { assignable_quantity: { "41": 4 } }),
            409,
          );
        },
      ],
    ]);
    await heading();
    const dialog = await openDialog();
    fillStep1(dialog, { 1: "6" });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));
    expect(await within(dialog).findByText("서버 확인: 배정 가능량 4 초과 — 수량을 4 이하로 고쳐 주세요.")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("라인 1 이번 선적 수량")).toHaveFocus(); // 첫 문제 칸
    fireEvent.change(within(dialog).getByLabelText("라인 1 이번 선적 수량"), { target: { value: "4" } });
    expect(within(dialog).queryByText(/배정 가능량 4 초과/)).toBeNull(); // 고치면 그 칸의 안내만 지운다
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));
    await waitFor(() => expect(creates).toBe(2));
    const keys = posts(calls, CREATE).map((c) => c.headers["Idempotency-Key"]);
    expect(keys[0]).not.toBe(keys[1]); // 본문이 바뀌면 새 키
  });

  it("결과 불명(5xx) — 같은 키로 재확정 안내, '뒤로'·Esc 막힘", async () => {
    const { calls } = open(poDetail({ lines: LINES }), [
      [PREVIEW, "POST", () => jsonResponse(importShipmentPreview({ po_id: 9, po_doc_number: "PO-2026-0001" }))],
      [CREATE, "POST", () => jsonResponse({ error: { code: "X", message: "게이트웨이 시간 초과", detail: {} } }, 504)],
    ]);
    await heading();
    const dialog = await openDialog();
    fillStep1(dialog, { 1: "6" });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));
    expect(await within(dialog).findByText(/생성 여부를 알 수 없습니다/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "뒤로" })).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "생성 확정" }));
    await waitFor(() => expect(posts(calls, CREATE)).toHaveLength(2));
    const keys = posts(calls, CREATE).map((c) => c.headers["Idempotency-Key"]);
    expect(keys[0]).toBe(keys[1]); // 같은 본문 = 같은 키(중복 수입선적 방지)
    expect(within(dialog).getByRole("button", { name: "확인 없이 닫기(수입선적 목록에서 확인)" })).toBeInTheDocument();
  });

  it("422 칸별 — LINE_MISMATCH `lines[i].po_line_id`는 보낸 i번째 라인 칸 아래, 메모 422는 메모 칸 아래", async () => {
    let round = 0;
    open(poDetail({ lines: LINES }), [
      [
        PREVIEW,
        "POST",
        () => {
          round += 1;
          return round === 1
            ? jsonResponse(
                apiErrorResponse("SHIPMENTS.SOURCE.LINE_MISMATCH", "선택한 라인이 이 선적의 원천 전표의 라인이 아닙니다.", {
                  "lines[1].po_line_id": "이 발주의 라인이 아닙니다.",
                }),
                422,
              )
            : jsonResponse(
                apiErrorResponse("COMMON.VALIDATION.INVALID_FIELD", "입력값을 확인해 주세요.", { internal_note: "보이지 않는 글자를 지워 주세요." }),
                422,
              );
        },
      ],
    ]);
    await heading();
    const dialog = await openDialog();
    fillStep1(dialog, { 1: "2", 2: "3" });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    const second = within(dialog).getByLabelText("라인 2 이번 선적 수량").closest("li") as HTMLElement;
    expect(await within(second).findByText("서버 확인: 이 발주의 라인이 아닙니다.")).toBeInTheDocument();
    const first = within(dialog).getByLabelText("라인 1 이번 선적 수량").closest("li") as HTMLElement;
    expect(first.textContent).not.toContain("이 발주의 라인이 아닙니다");
    fireEvent.change(within(dialog).getByLabelText("내부 메모 (선택)"), { target: { value: "메모" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    expect(await within(dialog).findByText("서버 확인: 보이지 않는 글자를 지워 주세요.")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("내부 메모 (선택)")).toHaveAttribute("aria-invalid", "true");
  });

  it("409 DOCUMENT_NOT_CONSUMABLE(그 사이 취소 등)은 '발주 다시 불러오기'", async () => {
    open(poDetail({ lines: LINES }), [
      [
        PREVIEW,
        "POST",
        () => jsonResponse(apiErrorResponse("TRADE_DOCS.QUANTITY.DOCUMENT_NOT_CONSUMABLE", "이 문서의 현재 상태에서는 수량을 사용할 수 없습니다."), 409),
      ],
    ]);
    await heading();
    const dialog = await openDialog();
    fillStep1(dialog, { 1: "1" });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "발주 다시 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

describe("이 발주의 수입선적 목록·취소 409", () => {
  it("금액 열 없음·'선적 확정' 배지·ETD/ETA 문자열 그대로", async () => {
    open(poDetail({ lines: LINES }), [
      [
        "/v1/shipments?po_id=9",
        "GET",
        () =>
          jsonResponse(
            page([
              importShipmentListItem({ status: "RELEASE_ORDERED", eta: { value: "2026-11-02", basis: "PLANNED" } }),
              importShipmentListItem({ id: 42, doc_number: "SH-2026-0012" }),
            ]),
          ),
      ],
    ]);
    await heading();
    const section = screen.getByRole("region", { name: "수입선적" });
    const link = await within(section).findByRole("link", { name: "SH-2026-0011" });
    expect(link).toHaveAttribute("href", "/shipments/41");
    const headers = within(section).getAllByRole("columnheader").map((th) => th.textContent);
    expect(headers).toEqual(["선적번호", "증빙일", "상태", "출발 → 도착", "ETD", "ETA", "라인"]);
    const row = link.closest("tr") as HTMLElement;
    expect(within(row).getByText("선적 확정")).toBeInTheDocument();
    expect(within(row).getByText("2026-11-02")).toBeInTheDocument();
    expect(section.textContent).not.toMatch(/0\.00|USD|합계|출고지시/);
  });

  it("살아 있는 수입선적이 있는 발주 취소 409 SUCCESSOR_ALIVE — '수입선적을 먼저 취소' + 선적 번호 링크", async () => {
    open(poDetail({ lines: LINES }), [
      [
        "/v1/purchase-orders/9/transitions",
        "POST",
        () =>
          jsonResponse(
            apiErrorResponse("TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE", "후속 전표가 살아 있어 취소할 수 없습니다.", { successors: ["SH-2026-0011", "SH-2026-0012"] }),
            409,
          ),
      ],
    ]);
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "공급 중단" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "취소 확정" }));
    expect(await within(dialog).findByText(/수입선적을 먼저 취소해 주세요/)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "SH-2026-0012" })).toHaveAttribute("href", "/shipments?q=SH-2026-0012");
  });
});
