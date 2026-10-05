// 선적 상세 — 헤더(고정 환율·국가·DG 요약)·가용재고 '미산정' 자리·allowed_actions 기반 버튼·출고지시/취소 대화상자(키·version)·
// 라인 수정 409 칸별 잔량·제외 LAST_LINE·당사자 추가(역할별 거래처 유형)/제외·문서 흐름 SHIPMENT·상태 이력·소스 계약 (S3-2 PR-3b, 그룹 A·J·K).
// fetch 스텁은 정확 URL·메서드 일치(stubGateFetch).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { stubGateFetch, type GateCall, type GateHandler } from "../test/gate-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";
import { soDetail, SO_LINE } from "../test/so-fixtures";
import { AUTO_CONSIGNEE, FORWARDER_PARTY, SHIPMENT_LINE, apiErrorResponse, shipmentDetail } from "../test/shipment-fixtures";

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

const SH = "/v1/shipments/31";
const LOG = {
  items: [
    {
      id: 1,
      occurred_at: "2026-10-04T01:00:00Z",
      from_status: null,
      to_status: "PLANNED",
      reason: null,
      actor_user_id: 1,
      actor_name: "무역 담당",
      automatic: false,
    },
  ],
  total: 1,
  page: 1,
  size: 50,
};

function refs(): GateHandler[] {
  return [
    [`${SH}/status-log`, "GET", () => jsonResponse(LOG)],
    ["/v1/document-flow/SHIPMENT/31", "GET", () => jsonResponse({ root_kind: "SALES_ORDER", root_id: 9, nodes: [] })],
    ["/v1/users/lookup?size=200", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }, { id: 2, display_name: "다른 담당" }]))],
    [
      "/v1/sales-orders/9",
      "GET",
      () =>
        jsonResponse(
          soDetail({
            status: "IN_SHIPMENT",
            lines: [
              { ...SO_LINE, shipment_open_quantity: 2 },
              { ...SO_LINE, id: 42, line_no: 2, sku_code: "SKU-002", sku_name_ko: "토너", shipment_open_quantity: 5 },
            ],
          }),
        ),
    ],
    [SH, "GET", () => jsonResponse(server.detail)],
  ];
}

/** 서버 상태 — 쓰기 응답이 오면 갱신해 이어지는 재조회(무효화)가 최신 값을 받게 한다(실서버와 같은 순서). */
const server = { detail: shipmentDetail() };
const writes = (next: ReturnType<typeof shipmentDetail>, status = 200) => () => {
  server.detail = next;
  return jsonResponse(next, status);
};

function open(detail = shipmentDetail(), me: unknown = TRADER, extra: GateHandler[] = []) {
  server.detail = detail;
  const stub = stubGateFetch(me, [...extra, ...refs()]);
  renderWithProviders(<AppRoutes />, { route: "/shipments/31" });
  return stub;
}

const heading = () => screen.findByRole("heading", { name: /SH-2026-0001/ });
const sent = (calls: GateCall[], path: string, method: string) => calls.filter((c) => c.method === method && c.url === `/api${path}`);

describe("선적 상세 — 표시", () => {
  it("헤더: 원천 수주 링크·거래 상대·국가 이름(코드)·증빙일 문자열·고정 환율 보조문·결제조건·인코텀즈·합계·담당·KST 시각", async () => {
    open();
    await heading();
    const info = screen.getByRole("region", { name: "선적 정보" });
    expect(within(info).getByRole("link", { name: "SO-2026-0001" })).toHaveAttribute("href", "/sales-orders/9");
    expect(within(info).getByText("(선적중)")).toBeInTheDocument();
    expect(within(info).getByText("ABC Trading")).toBeInTheDocument();
    expect(within(info).getByText("대한민국 (KR)")).toBeInTheDocument();
    expect(within(info).getByText("미국 (US)")).toBeInTheDocument();
    expect(within(info).getByText("2026-10-04")).toBeInTheDocument();
    expect(within(info).getByText("1350.5 (기준일 2026-09-29)")).toBeInTheDocument();
    expect(within(info).getByText(/수주에서 고정 — 바꾸려면 취소 후 새로 만듭니다/)).toBeInTheDocument();
    expect(within(info).getByText("후불 T/T · 잔금 B/L일 기준 30일")).toBeInTheDocument();
    expect(within(info).getByText("FOB Busan (2020)")).toBeInTheDocument();
    expect(within(info).getByText("50.00 USD")).toBeInTheDocument();
    expect(within(info).getByText("2026. 10. 04. 11:30 (KST)")).toBeInTheDocument(); // updated_at UTC 02:30 → KST
    expect(screen.queryByText(/2026-10-04T02:30/)).not.toBeInTheDocument();
  });

  it("라인: SKU·수량·원천 라인·선적 잔량(서버값)·단가·금액, 가용재고는 '미산정' 배지뿐(숫자·0 없음)", async () => {
    open();
    await heading();
    const lines = screen.getByRole("region", { name: "라인" });
    const row = within(lines).getByText("SKU-001").closest("tr") as HTMLElement;
    expect(within(row).getByText("수분 세럼")).toBeInTheDocument();
    expect(within(row).getByText("#1 · 수주 6")).toBeInTheDocument();
    expect(within(row).getByText("2")).toBeInTheDocument(); // remaining_after
    expect(within(row).getByText("12.50")).toBeInTheDocument();
    expect(within(row).getByText("가용재고 미산정")).toBeInTheDocument();
    const availabilityCell = within(row).getByText("가용재고 미산정").closest("td") as HTMLElement;
    expect(availabilityCell.textContent).toBe("가용재고 미산정");
  });

  it("DG 라인은 배지 + UN·등급, 헤더에 위험물 라인 수와 수동 점검 안내(차단 없음)", async () => {
    open(
      shipmentDetail({
        dg_line_count: 1,
        lines: [{ ...SHIPMENT_LINE, dg: { flag: true, un_number: "UN1950", dg_class: "2.1" } }],
      }),
    );
    await heading();
    expect(screen.getByText("위험물 라인 1개")).toBeInTheDocument();
    expect(screen.getByText(/위험물 선적 점검은 담당자가 수동으로 확인합니다/)).toBeInTheDocument();
    const row = within(screen.getByRole("region", { name: "라인" })).getByText("SKU-001").closest("tr") as HTMLElement;
    expect(within(row).getByText("DG")).toBeInTheDocument();
    expect(within(row).getByText("UN1950 · 2.1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "출고지시" })).toBeEnabled(); // DG는 차단하지 않는다
  });

  it("당사자: 역할 한글·영문 스냅샷·자동 행은 '원천에서 복사'이고 제외 버튼이 없다", async () => {
    open(shipmentDetail({ parties: [AUTO_CONSIGNEE, FORWARDER_PARTY] }));
    await heading();
    const auto = screen.getByText("ABC Trading Inc.").closest("tr") as HTMLElement;
    expect(within(auto).getByText("수하인")).toBeInTheDocument();
    expect(within(auto).getByText("원천에서 복사")).toBeInTheDocument();
    expect(within(auto).queryByRole("button", { name: "제외" })).toBeNull();
    const fwd = screen.getByText("Fast Forwarding Co.").closest("tr") as HTMLElement;
    expect(within(fwd).getByText("포워더")).toBeInTheDocument();
    expect(within(fwd).getByRole("button", { name: "제외" })).toBeInTheDocument();
  });

  it("문서 흐름은 SHIPMENT 종류로, 상태 이력은 선적 라벨로 보인다", async () => {
    const { calls } = open();
    await heading();
    expect(await screen.findByText("계획 (작성)")).toBeInTheDocument();
    await waitFor(() => expect(sent(calls, "/v1/document-flow/SHIPMENT/31", "GET")).toHaveLength(1));
  });

  // 수입선적 상세(금액 칸 0·배정 가능·선적 확정 문구·라인 추가/수정)는 shipment-detail.import.test.tsx(S3-2 PR-5b).

  it("없는 선적은 404 문구와 목록 링크", async () => {
    stubGateFetch(TRADER, [[SH, "GET", () => jsonResponse(apiErrorResponse("COMMON.RESOURCE.NOT_FOUND", "없음"), 404)]]);
    renderWithProviders(<AppRoutes />, { route: "/shipments/31" });
    expect(await screen.findByText("선적을 찾을 수 없습니다.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "선적 목록으로" })).toHaveAttribute("href", "/shipments");
  });
});

describe("선적 상세 — 역할·상태별 버튼은 서버 allowed_actions만 따른다", () => {
  it("물류(L): 출고지시·국가·메모·당사자 — 취소·라인 편집 없음, 담당자는 읽기(lookup API 미호출)", async () => {
    const { calls } = open(
      shipmentDetail({ allowed_actions: ["RELEASE_ORDER", "EDIT_COUNTRIES", "EDIT_META", "EDIT_PARTIES"] }),
      LOGISTICS,
    );
    await heading();
    expect(screen.getByRole("button", { name: "출고지시" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "취소" })).toBeNull();
    expect(screen.queryByRole("button", { name: "수정" })).toBeNull();
    expect(screen.queryByRole("form", { name: "라인 추가" })).toBeNull();
    expect(screen.getByRole("form", { name: "당사자 추가" })).toBeInTheDocument();
    expect(screen.getByLabelText("출발국 (필수)")).toHaveValue("KR");
    expect(screen.getByText("담당자 변경은 무역 담당·관리자가 합니다.")).toBeInTheDocument();
    expect(sent(calls, "/v1/users/lookup?size=200", "GET")).toHaveLength(0);
    expect(sent(calls, "/v1/sales-orders/9", "GET")).toHaveLength(0); // 라인 추가 폼이 없으면 원천 수주도 안 부른다
  });

  it("조회(V): 쓰기 버튼·폼 0, 메모는 읽기 문구", async () => {
    open(shipmentDetail({ allowed_actions: [], internal_note: "포장 주의" }), VIEWER);
    await heading();
    for (const name of ["출고지시", "취소", "수정", "제외", "저장", "당사자 추가", "라인 추가"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    expect(screen.getByText("내부 메모: 포장 주의")).toBeInTheDocument();
  });

  it("출고지시된 선적: 서버가 EDIT_LINES·RELEASE_ORDER를 빼면 버튼도 없다(화면이 상태를 다시 판정하지 않는다)", async () => {
    open(
      shipmentDetail({
        status: "RELEASE_ORDERED",
        frozen_at: "2026-10-04T03:00:00Z",
        allowed_actions: ["CANCEL", "EDIT_META", "EDIT_PARTIES"],
      }),
    );
    await heading();
    expect(screen.getByText(/출고지시된 선적입니다/)).toBeInTheDocument();
    expect(screen.getByText("2026. 10. 04. 12:00 (KST)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "출고지시" })).toBeNull();
    expect(screen.queryByRole("button", { name: "수정" })).toBeNull();
    expect(screen.queryByLabelText("출발국 (필수)")).toBeNull();
    expect(screen.getByRole("button", { name: "취소" })).toBeInTheDocument();
  });
});

describe("선적 상세 — 출고지시·취소 대화상자", () => {
  it("출고지시: 확인 문구 → POST release-order {version}·대화상자 키 1개, 성공하면 응답으로 화면 교체", async () => {
    const released = shipmentDetail({ status: "RELEASE_ORDERED", version: 4, frozen_at: "2026-10-04T03:00:00Z", allowed_actions: ["CANCEL", "EDIT_META", "EDIT_PARTIES"] });
    const { calls } = open(shipmentDetail(), TRADER, [[`${SH}/release-order`, "POST", writes(released)]]);
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "출고지시" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/라인·국가를 바꿀 수 없습니다/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "출고지시 확정" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "출고지시 확정" })); // 더블클릭 — 동기 잠금
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const posts = sent(calls, `${SH}/release-order`, "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0]!.body).toEqual({ version: 3 });
    expect(posts[0]!.headers["Idempotency-Key"]).toMatch(/^key-/);
    expect(await screen.findByText(/출고지시된 선적입니다/)).toBeInTheDocument();
  });

  it("취소: 사유 빈칸이면 확정 불가 → 사유와 함께 POST transitions {to_status, version, reason}; 409 version은 대화상자 안 '최신 내용 불러오기'", async () => {
    let attempt = 0;
    const { calls } = open(shipmentDetail(), TRADER, [
      [
        `${SH}/transitions`,
        "POST",
        () => {
          attempt += 1;
          return jsonResponse(apiErrorResponse("COMMON.CONCURRENCY.VERSION_CONFLICT", "다른 사용자가 먼저 수정했습니다."), 409);
        },
      ],
    ]);
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "취소 확정" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "바이어 일정 변경" } });
    fireEvent.click(confirm);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("다른 곳에서 이 선적 정보가 먼저 수정되었습니다");
    expect(within(dialog).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
    // 같은 본문 재시도 = 같은 키.
    fireEvent.click(within(dialog).getByRole("button", { name: "취소 확정" }));
    await waitFor(() => expect(attempt).toBe(2));
    const posts = sent(calls, `${SH}/transitions`, "POST");
    expect(posts[0]!.body).toEqual({ to_status: "CANCELLED", version: 3, reason: "바이어 일정 변경" });
    expect(posts[1]!.headers["Idempotency-Key"]).toBe(posts[0]!.headers["Idempotency-Key"]);
  });

  it("확인창 처리 중(pending) Esc를 눌러도 닫히지 않는다 — 응답이 오면 결과로 닫힌다(적대 검토 low ⑨)", async () => {
    let release: (value: Response) => void = () => undefined;
    const released = shipmentDetail({ status: "RELEASE_ORDERED", version: 4, frozen_at: "2026-10-04T03:00:00Z", allowed_actions: ["CANCEL", "EDIT_META", "EDIT_PARTIES"] });
    open(shipmentDetail(), TRADER, [
      [
        `${SH}/release-order`,
        "POST",
        () =>
          new Promise<Response>((resolve) => {
            release = (value) => {
              server.detail = released;
              resolve(value);
            };
          }),
      ],
    ]);
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "출고지시" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "출고지시 확정" }));
    expect(await within(dialog).findByRole("button", { name: "처리 중…" })).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    release(jsonResponse(released));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByText(/출고지시된 선적입니다/)).toBeInTheDocument();
  });

  it("취소를 닫고 다시 열면 새 키(대화상자 1회 = 키 1개)", async () => {
    const { calls } = open(shipmentDetail(), TRADER, [
      [`${SH}/transitions`, "POST", () => jsonResponse(apiErrorResponse("X.Y.Z", "실패"), 422)],
    ]);
    await heading();
    for (let round = 0; round < 2; round += 1) {
      fireEvent.click(screen.getByRole("button", { name: "취소" }));
      const dialog = screen.getByRole("dialog");
      fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "사유" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "취소 확정" }));
      await within(dialog).findByRole("alert");
      fireEvent.click(within(dialog).getByRole("button", { name: "닫기" }));
    }
    const posts = sent(calls, `${SH}/transitions`, "POST");
    expect(posts).toHaveLength(2);
    expect(posts[1]!.headers["Idempotency-Key"]).not.toBe(posts[0]!.headers["Idempotency-Key"]);
  });
});

describe("선적 상세 — 라인·메모·당사자 쓰기", () => {
  it("라인 수량 수정: PATCH {version, quantity} · 409 EXCEEDS_OPEN이면 칸 아래 '이 라인 최대 수량 N'(원천 잔량 + 현재)·선적·수주 재조회", async () => {
    const { calls } = open(shipmentDetail(), TRADER, [
      [
        `${SH}/lines/501`,
        "PATCH",
        () =>
          jsonResponse(
            apiErrorResponse("TRADE_DOCS.QUANTITY.EXCEEDS_OPEN", "원천 남은 수량을 넘습니다.", { open_quantity: { "41": 2 } }),
            409,
          ),
      ],
    ]);
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    const form = screen.getByRole("form", { name: "라인 1 수정" });
    fireEvent.change(within(form).getByRole("textbox"), { target: { value: "9" } });
    const before = { shipment: sent(calls, SH, "GET").length, so: sent(calls, "/v1/sales-orders/9", "GET").length };
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));
    const alert = await screen.findByText(/원천 남은 수량을 넘습니다/);
    expect(alert).toHaveTextContent("— 이 라인 최대 수량 2");
    expect(alert).not.toHaveTextContent("원천 남은 수량 2");
    expect(sent(calls, `${SH}/lines/501`, "PATCH")[0]!.body).toEqual({ version: 3, quantity: 9 });
    // 409 뒤 선적·원천 수주를 다시 받아 상한 안내(원천 잔량 + 현재)를 서버 값으로(low ⑥).
    await waitFor(() => expect(sent(calls, SH, "GET").length).toBeGreaterThan(before.shipment));
    await waitFor(() => expect(sent(calls, "/v1/sales-orders/9", "GET").length).toBeGreaterThan(before.so));
  });

  it("라인 제외: DELETE ?version= · 마지막 라인 409 LAST_LINE은 대화상자 안에 서버 문구(선적 취소 안내)", async () => {
    const { calls } = open(shipmentDetail(), TRADER, [
      [
        `${SH}/lines/501?version=3`,
        "DELETE",
        () =>
          jsonResponse(
            apiErrorResponse("SHIPMENTS.LINE.LAST_LINE", "선적에는 라인이 1개 이상 있어야 합니다. 선적 전체를 없애려면 선적을 취소해 주세요."),
            409,
          ),
      ],
    ]);
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "제외" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "제외" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("선적을 취소해 주세요");
    expect(sent(calls, `${SH}/lines/501?version=3`, "DELETE")).toHaveLength(1);
  });

  it("라인 추가: 이 선적에 없는 원천 라인만 고를 수 있고 [잔량 전부] → POST {version, source_line_id, quantity}·키 1개", async () => {
    const added = shipmentDetail({ version: 4, lines: [SHIPMENT_LINE, { ...SHIPMENT_LINE, id: 502, line_no: 2, so_line_id: 42, quantity: 5 }] });
    const { calls } = open(shipmentDetail(), TRADER, [[`${SH}/lines`, "POST", writes(added, 201)]]);
    await heading();
    const form = await screen.findByRole("form", { name: "라인 추가" });
    const select = await within(form).findByRole("combobox");
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["선택", "#2 SKU-002 토너 — 선적 잔량 5"]); // 라인 41은 이미 이 선적에 있다
    fireEvent.change(select, { target: { value: "42" } });
    fireEvent.click(within(form).getByRole("button", { name: "잔량 전부" }));
    fireEvent.click(within(form).getByRole("button", { name: "라인 추가" }));
    await waitFor(() => expect(sent(calls, `${SH}/lines`, "POST")).toHaveLength(1));
    const post = sent(calls, `${SH}/lines`, "POST")[0]!;
    expect(post.body).toEqual({ version: 3, source_line_id: 42, quantity: 5 });
    expect(post.headers["Idempotency-Key"]).toMatch(/^key-/);
  });

  it("라인 추가 409 EXCEEDS_OPEN 뒤 원천 수주를 다시 받아 드롭다운의 선적 잔량이 서버 값으로 바뀐다", async () => {
    let conflicted = false;
    const so = () =>
      soDetail({
        status: "IN_SHIPMENT",
        lines: [
          { ...SO_LINE, shipment_open_quantity: 2 },
          { ...SO_LINE, id: 42, line_no: 2, sku_code: "SKU-002", sku_name_ko: "토너", shipment_open_quantity: conflicted ? 3 : 5 },
        ],
      });
    open(shipmentDetail(), TRADER, [
      ["/v1/sales-orders/9", "GET", () => jsonResponse(so())],
      [
        `${SH}/lines`,
        "POST",
        () => {
          conflicted = true;
          return jsonResponse(apiErrorResponse("TRADE_DOCS.QUANTITY.EXCEEDS_OPEN", "원천 남은 수량을 넘습니다.", { open_quantity: { "42": 3 } }), 409);
        },
      ],
    ]);
    await heading();
    const form = await screen.findByRole("form", { name: "라인 추가" });
    const select = await within(form).findByRole("combobox");
    await within(select).findByRole("option", { name: "#2 SKU-002 토너 — 선적 잔량 5" });
    fireEvent.change(select, { target: { value: "42" } });
    fireEvent.change(within(form).getByRole("textbox"), { target: { value: "5" } });
    fireEvent.click(within(form).getByRole("button", { name: "라인 추가" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("— 원천 남은 수량 3");
    expect(await within(select).findByRole("option", { name: "#2 SKU-002 토너 — 선적 잔량 3" })).toBeInTheDocument();
    fireEvent.click(within(form).getByRole("button", { name: "잔량 전부" }));
    expect(within(form).getByRole("textbox")).toHaveValue("3");
  });

  it("메모·담당·국가: 바뀐 필드만 PATCH, 모르는 국가 코드·별칭 코드는 저장 불가", async () => {
    const { calls } = open(shipmentDetail(), TRADER, [[SH, "PATCH", writes(shipmentDetail({ version: 4, dest_country_code: "JP" }))]]);
    await heading();
    const form = screen.getByRole("form", { name: "메모·담당·국가" });
    const save = within(form).getByRole("button", { name: "저장" });
    expect(save).toBeDisabled();
    fireEvent.change(within(form).getByLabelText("도착국 (필수)"), { target: { value: "xx" } });
    expect(within(form).getByLabelText("도착국 (필수)")).toHaveValue("XX");
    expect(within(form).getByText(/알 수 없는 국가 코드입니다/)).toBeInTheDocument();
    expect(save).toBeDisabled();
    // CLDR 별칭(UK·DD·SU)은 이름이 붙어도 ISO 정식 코드가 아니다 — 정식 코드 안내·저장 불가(적대 검토 med ①).
    for (const [alias, canonical] of [["UK", "GB"], ["DD", "DE"], ["SU", "RU"]] as const) {
      fireEvent.change(within(form).getByLabelText("도착국 (필수)"), { target: { value: alias } });
      expect(within(form).getByText(`${alias}는 ISO 정식 코드가 아닙니다 — ${canonical}로 입력해 주세요.`)).toBeInTheDocument();
      expect(save).toBeDisabled();
    }
    fireEvent.change(within(form).getByLabelText("도착국 (필수)"), { target: { value: "jp" } });
    expect(within(form).getByText("일본")).toBeInTheDocument();
    fireEvent.click(save);
    await waitFor(() => expect(sent(calls, SH, "PATCH")).toHaveLength(1));
    expect(sent(calls, SH, "PATCH")[0]!.body).toEqual({ version: 3, dest_country_code: "JP" });
  });

  it("당사자 추가: 역할을 고르면 그 유형 거래처만 검색(포워더 → type=FORWARDER) → POST {role, partner_id}", async () => {
    const { calls } = open(shipmentDetail(), TRADER, [
      ["/v1/partners?type=FORWARDER&size=20", "GET", () => jsonResponse(page([{ id: 8, name_ko: "빠른포워딩", name_en: "Fast Forwarding Co.", type_codes: ["FORWARDER"] }]))],
      [`${SH}/parties`, "POST", writes(shipmentDetail({ parties: [AUTO_CONSIGNEE, FORWARDER_PARTY] }), 201)],
    ]);
    await heading();
    const form = screen.getByRole("form", { name: "당사자 추가" });
    const role = within(form).getByRole("combobox", { name: "역할" });
    expect(within(role).getAllByRole("option").map((o) => o.textContent)).toEqual(["선택", "통지처", "포워더", "관세사"]);
    fireEvent.change(role, { target: { value: "FORWARDER" } });
    const box = within(form).getByRole("combobox", { name: "포워더 거래처" });
    fireEvent.focus(box);
    fireEvent.click(await screen.findByRole("option", { name: "빠른포워딩 (Fast Forwarding Co.)" }));
    fireEvent.click(within(form).getByRole("button", { name: "당사자 추가" }));
    await waitFor(() => expect(sent(calls, `${SH}/parties`, "POST")).toHaveLength(1));
    expect(sent(calls, `${SH}/parties`, "POST")[0]!.body).toEqual({ role: "FORWARDER", partner_id: 8 });
    expect(await within(screen.getByRole("region", { name: "당사자" })).findByText("Fast Forwarding Co.")).toBeInTheDocument();
  });

  it("당사자 추가 422 영문명 결측(무역): 서버 문구 + 실재하는 고치는 길(거래처 CSV 내보내기 → 엑셀 임포트) 링크", async () => {
    open(shipmentDetail(), TRADER, [
      ["/v1/partners?size=20", "GET", () => jsonResponse(page([{ id: 9, name_ko: "영문없는 상사", name_en: null, type_codes: ["BUYER"] }]))],
      [
        `${SH}/parties`,
        "POST",
        () => jsonResponse(apiErrorResponse("SHIPMENTS.PARTY.ENGLISH_NAME_MISSING", "거래처 영문명을 확인해 주세요.", { partner_id: 9 }), 422),
      ],
    ]);
    await heading();
    const form = screen.getByRole("form", { name: "당사자 추가" });
    fireEvent.change(within(form).getByRole("combobox", { name: "역할" }), { target: { value: "NOTIFY" } });
    fireEvent.focus(within(form).getByRole("combobox", { name: "통지처 거래처" }));
    fireEvent.click(await screen.findByRole("option", { name: "영문없는 상사 — 영문명 없음" }));
    fireEvent.click(within(form).getByRole("button", { name: "당사자 추가" }));
    const alert = await within(form).findByRole("alert");
    expect(alert).toHaveTextContent("거래처 영문명을 확인해 주세요.");
    // 거래처 수정 화면·API는 없다(P-44) — 실재 경로는 거래처 CSV 내보내기 → 엑셀 임포트(대상: 거래처).
    expect(within(alert).getByRole("link", { name: "거래처" })).toHaveAttribute("href", "/partners");
    expect(within(alert).getByRole("link", { name: "엑셀 임포트" })).toHaveAttribute("href", "/imports");
    expect(alert).toHaveTextContent("CSV 내보내기");
  });

  async function partyAddFails(me: unknown, error: { code: string; message: string; detail: Record<string, unknown> }) {
    open(shipmentDetail({ allowed_actions: ["RELEASE_ORDER", "EDIT_COUNTRIES", "EDIT_META", "EDIT_PARTIES"] }), me, [
      ["/v1/partners?type=FORWARDER&size=20", "GET", () => jsonResponse(page([{ id: 9, name_ko: "어떤 상사", name_en: "Some Co", type_codes: ["BUYER"] }]))],
      [`${SH}/parties`, "POST", () => jsonResponse(apiErrorResponse(error.code, error.message, error.detail), 422)],
    ]);
    await heading();
    const form = screen.getByRole("form", { name: "당사자 추가" });
    fireEvent.change(within(form).getByRole("combobox", { name: "역할" }), { target: { value: "FORWARDER" } });
    fireEvent.focus(within(form).getByRole("combobox", { name: "포워더 거래처" }));
    fireEvent.click(await screen.findByRole("option", { name: "어떤 상사 (Some Co)" }));
    fireEvent.click(within(form).getByRole("button", { name: "당사자 추가" }));
    return within(form).findByRole("alert");
  }

  it("당사자 추가 422 영문명 결측(물류): '무역 담당에게 요청' 문구만 — 링크 0(물류는 임포트 권한이 없다)", async () => {
    const alert = await partyAddFails(LOGISTICS, {
      code: "SHIPMENTS.PARTY.ENGLISH_NAME_MISSING",
      message: "거래처 영문명을 확인해 주세요.",
      detail: { partner_id: 9 },
    });
    expect(alert).toHaveTextContent("무역 담당에게 거래처 영문명 등록을 요청하세요.");
    expect(within(alert).queryAllByRole("link")).toHaveLength(0);
  });

  it("거래처 유형 불일치 422(INVALID_FIELD)는 서버 문구만 — 고치기 링크·안내 0(주소 문제와 코드로 구분 불가)", async () => {
    const alert = await partyAddFails(TRADER, {
      code: "COMMON.VALIDATION.INVALID_FIELD",
      message: "입력값을 확인해 주세요.",
      detail: { partner_id: "포워더 유형의 거래처가 아닙니다. 거래처 유형을 확인해 주세요." },
    });
    expect(alert).toHaveTextContent("포워더 유형의 거래처가 아닙니다");
    expect(within(alert).queryAllByRole("link")).toHaveLength(0);
    expect(alert).not.toHaveTextContent("엑셀 임포트");
  });

  // 적대 검토 med ④ — 당사자 쓰기는 헤더 version을 검사·증가하지 않고 서버 최신 version을 돌려준다. 그 값으로 기준을 옮기면
  // 낡은 화면의 메모 폼이 409 없이 남의 수정을 덮어썼다.
  it("낡은 화면에서 당사자 추가 → 응답의 최신 version으로 기준을 옮기지 않는다: 배너가 뜨고 메모 저장은 옛 version으로 나가 409", async () => {
    const { calls } = open(shipmentDetail(), TRADER, [
      // 그 사이 다른 사람이 메모를 바꿔 서버는 v4 — 당사자 추가 응답이 그 값을 싣는다.
      [`${SH}/parties`, "POST", writes(shipmentDetail({ version: 4, internal_note: "다른 사람 메모", parties: [AUTO_CONSIGNEE, FORWARDER_PARTY] }), 201)],
      ["/v1/partners?type=FORWARDER&size=20", "GET", () => jsonResponse(page([{ id: 8, name_ko: "빠른포워딩", name_en: "Fast Forwarding Co.", type_codes: ["FORWARDER"] }]))],
      [SH, "PATCH", () => jsonResponse(apiErrorResponse("COMMON.CONCURRENCY.VERSION_CONFLICT", "다른 사용자가 먼저 수정했습니다."), 409)],
    ]);
    await heading();
    const partyForm = screen.getByRole("form", { name: "당사자 추가" });
    fireEvent.change(within(partyForm).getByRole("combobox", { name: "역할" }), { target: { value: "FORWARDER" } });
    fireEvent.focus(within(partyForm).getByRole("combobox", { name: "포워더 거래처" }));
    fireEvent.click(await screen.findByRole("option", { name: "빠른포워딩 (Fast Forwarding Co.)" }));
    fireEvent.click(within(partyForm).getByRole("button", { name: "당사자 추가" }));
    expect(await screen.findByText(/다른 곳에서 이 선적이 수정되었습니다/)).toBeInTheDocument();

    const meta = screen.getByRole("form", { name: "메모·담당·국가" });
    fireEvent.change(within(meta).getByLabelText("내부 메모"), { target: { value: "내 메모" } });
    fireEvent.click(within(meta).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(sent(calls, SH, "PATCH")).toHaveLength(1));
    expect(sent(calls, SH, "PATCH")[0]!.body).toMatchObject({ version: 3, internal_note: "내 메모" });
    expect(await screen.findByText(/다른 곳에서 이 선적 정보가 먼저 수정되었습니다/)).toBeInTheDocument();
  });

  it("당사자 제외: DELETE ?version=당사자 version(헤더 version 아님)", async () => {
    const { calls } = open(shipmentDetail({ parties: [AUTO_CONSIGNEE, FORWARDER_PARTY] }), TRADER, [
      [`${SH}/parties/72?version=2`, "DELETE", writes(shipmentDetail({ parties: [AUTO_CONSIGNEE] }))],
    ]);
    await heading();
    const fwd = screen.getByText("Fast Forwarding Co.").closest("tr") as HTMLElement;
    fireEvent.click(within(fwd).getByRole("button", { name: "제외" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "제외" }));
    await waitFor(() => expect(sent(calls, `${SH}/parties/72?version=2`, "DELETE")).toHaveLength(1));
  });
});

describe("선적 화면 — 소스 계약 (design-D D12·D13, PR-2b R-2b-2 승계 — PR-4b: 마일스톤 컴포넌트·lib 편입)", () => {
  const sources = import.meta.glob(
    [
      "./shipments.tsx",
      "./shipment-*.tsx",
      "../components/shipment-*.tsx",
      "../lib/shipment.ts",
      "../components/milestone-*.tsx",
      "../lib/milestone.ts",
    ],
    { query: "?raw", import: "default", eager: true },
  ) as Record<string, string>;
  const code = (src: string) => src.replace(/\/\/.*$/gm, "").replace(/\{\/\*[\s\S]*?\*\/\}/g, "");

  it("스캔 대상이 실제로 있다(공회전 방지) — 테스트 파일 제외 12개(마일스톤 6 컴포넌트 + lib 포함)", () => {
    expect(Object.keys(sources).filter((path) => !path.includes(".test.")).sort()).toEqual([
      "../components/milestone-changes.tsx",
      "../components/milestone-customs.tsx",
      "../components/milestone-dialogs.tsx",
      "../components/milestone-oem-section.tsx",
      "../components/milestone-set-editor.tsx",
      "../components/milestone-timeline.tsx",
      "../components/shipment-create-dialog.tsx",
      "../components/shipment-parts.tsx",
      "../lib/milestone.ts",
      "../lib/shipment.ts",
      "./shipment-detail.tsx",
      "./shipments.tsx",
    ]);
  });

  it("날짜 문자열을 시각 객체로 바꾸지 않는다 — `new Date(` 0·직접 fetch 0", () => {
    const offenders = Object.entries(sources)
      .filter(([path]) => !path.includes(".test."))
      .filter(([, src]) => /new Date\(|\bfetch\(/.test(code(src)))
      .map(([path]) => path);
    expect(offenders).toEqual([]);
  });

  it("표는 섹션 안 overflow-x-auto 래퍼 안에서만 가로 스크롤, 머리글은 전부 cell-nowrap", () => {
    for (const [path, src] of Object.entries(sources)) {
      if (path.includes(".test.")) continue;
      const text = code(src);
      const tables = (text.match(/<table/g) ?? []).length;
      expect((text.match(/overflow-x-auto/g) ?? []).length, path).toBeGreaterThanOrEqual(tables);
      for (const th of text.match(/<th[\s>][^>]*>/g) ?? []) expect(th, path).toContain("cell-nowrap");
    }
  });

  it("390px에서 품명·영문 이름·주소 칸이 글자 단위로 꺾이지 않게 최소 폭을 둔다(실브라우저 발견 — 표는 래퍼 안에서 가로 스크롤)", () => {
    const heads = Object.entries(sources)
      .filter(([path]) => !path.includes(".test."))
      .flatMap(([, src]) => code(src).match(/<th[\s>][^>]*>(품명|거래처\(영문\)|주소\(영문\))<\/th>/g) ?? []);
    expect(heads.length).toBe(5); // PR-5b: 수입 미리보기 표의 품명 칸 +1
    for (const th of heads) expect(th).toMatch(/min-w-\d+/);
  });
});
