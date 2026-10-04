// SO 상세의 선적 연동 (S3-2 PR-3b — design-D D7·D8·D4 / R-02) — '선적 잔량' 열·선적 섹션·'선적 만들기' 노출 조건·2단 대화상자
// (빈칸 기본·[잔량 전부]·국가 필수·미리보기 본문·생성 키·409 칸별 잔량)·선적중 안내·SO 취소 409 '먼저 취소할 선적' 링크. 그룹 A·J.
// fetch 스텁은 정확 URL·메서드 일치(stubGateFetch) — 본문·경로 오류는 404로 터진다.

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { ADMIN } from "../test/approval-fixtures";
import { stubGateFetch, type GateCall, type GateHandler } from "../test/gate-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";
import { SO_LINE, SO_LOG, chainFlow, soDetail } from "../test/so-fixtures";
import { apiErrorResponse, shipmentDetail, shipmentListItem, shipmentPreview } from "../test/shipment-fixtures";

const LOGISTICS = { ...TRADER, id: 5, roles: ["LOGISTICS"] };

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const TONER = { ...SO_LINE, id: 42, line_no: 2, sku_code: "SKU-002", sku_name_ko: "토너", quantity: 5 };
const confirmed = (over: Parameters<typeof soDetail>[0] = {}) =>
  soDetail({
    status: "CONFIRMED",
    confirmed_at: "2026-09-30T02:00:00Z",
    lines: [
      { ...SO_LINE, shipment_open_quantity: 6 },
      { ...TONER, shipment_open_quantity: 0 },
    ],
    ...over,
  });

/** SO 상세가 마운트 때 부르는 공통 호출 — 게이트·확정 패널 호출은 스텁에 없으면 404(패널 안 오류로만 보인다). */
function base(so: ReturnType<typeof soDetail>, shipments = page([shipmentListItem()])): GateHandler[] {
  return [
    ["/v1/sales-orders/9", "GET", () => jsonResponse(so)],
    ["/v1/sales-orders/9/status-log", "GET", () => jsonResponse(SO_LOG)],
    ["/v1/document-flow/SALES_ORDER/9", "GET", () => jsonResponse(chainFlow("SALES_ORDER"))],
    ["/v1/users/lookup?size=200", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }]))],
    ["/v1/shipments?so_id=9", "GET", () => jsonResponse(shipments)],
  ];
}

function open(so = confirmed(), me: unknown = TRADER, extra: GateHandler[] = [], shipments = page([shipmentListItem()])) {
  const stub = stubGateFetch(me, [...extra, ...base(so, shipments)]);
  renderWithProviders(<AppRoutes />, { route: "/sales-orders/9" });
  return stub;
}

const heading = () => screen.findByRole("heading", { name: /SO-2026-0001/ });
const sent = (calls: GateCall[], path: string, method: string) => calls.filter((c) => c.method === method && c.url === `/api${path}`);
const shipSection = () => screen.getByRole("region", { name: "선적" });

describe("SO 상세 — 선적 섹션·선적 잔량", () => {
  it("라인 표에 '선적 잔량'(서버 파생값) 열, 선적 섹션에 이 수주의 선적(링크·상태·국가·합계)", async () => {
    const { calls } = open();
    await heading();
    const lines = screen.getByRole("region", { name: "라인" });
    expect(within(lines).getByRole("columnheader", { name: "선적 잔량" })).toBeInTheDocument();
    const toner = within(lines).getByText("SKU-002").closest("tr") as HTMLElement;
    expect(within(toner).getAllByText("0").length).toBeGreaterThan(0);

    const section = shipSection();
    expect(await within(section).findByRole("link", { name: "SH-2026-0001" })).toHaveAttribute("href", "/shipments/31");
    expect(within(section).getByText("계획")).toBeInTheDocument();
    expect(within(section).getByLabelText("출발국 KR 도착국 US")).toBeInTheDocument();
    expect(sent(calls, "/v1/shipments?so_id=9", "GET")).toHaveLength(1);
  });

  it("선적 섹션 ETD·ETA 열(부채 R-3b-3) — 서버 유효값 문자열 + 실적/예정 표지, 없으면 '—'", async () => {
    open(confirmed(), TRADER, [], page([shipmentListItem({ etd: null, eta: { value: "2026-10-21", basis: "ACTUAL" } })]));
    await heading();
    const section = shipSection();
    await within(section).findByRole("link", { name: "SH-2026-0001" });
    const headers = within(section).getAllByRole("columnheader").map((th) => th.textContent);
    const cells = within(within(section).getByRole("link", { name: "SH-2026-0001" }).closest("tr") as HTMLElement).getAllByRole("cell");
    expect(cells[headers.indexOf("ETD")]).toHaveTextContent(/^—$/);
    expect(cells[headers.indexOf("ETA")]).toHaveTextContent("2026-10-21 실적");
  });

  it("'선적 만들기'는 무역·관리자 + 확정·선적중 + 잔량 합 > 0일 때만", async () => {
    open();
    await heading();
    expect(within(shipSection()).getByRole("button", { name: "선적 만들기" })).toBeInTheDocument();
  });

  it.each([
    ["접수 수주", confirmed({ status: "RECEIVED", confirmed_at: null }), TRADER, "확정된 수주에서만 선적을 만들 수 있습니다."],
    ["잔량 0", confirmed({ lines: [{ ...SO_LINE, shipment_open_quantity: 0 }] }), TRADER, /선적 잔량이 없습니다/],
    ["조회 역할", confirmed(), VIEWER, "선적은 무역 담당·관리자가 만듭니다."],
    ["물류 역할", confirmed(), LOGISTICS, "선적은 무역 담당·관리자가 만듭니다."],
  ] as const)("%s → 버튼 없음·이유 안내", async (_name, so, me, reason) => {
    open(so, me);
    await heading();
    expect(within(shipSection()).queryByRole("button", { name: "선적 만들기" })).toBeNull();
    expect(within(shipSection()).getByText(reason)).toBeInTheDocument();
  });

  it("선적중 수주: 보류·취소 버튼이 없고 '선적을 먼저 취소' 안내, 선적 만들기는 잔량이 있으면 계속 가능", async () => {
    open(confirmed({ status: "IN_SHIPMENT" }));
    await heading();
    expect(screen.getByText(/선적이 살아 있는 동안 보류·취소할 수 없습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "보류" })).toBeNull();
    expect(screen.queryByRole("button", { name: "취소" })).toBeNull();
    expect(within(shipSection()).getByRole("button", { name: "선적 만들기" })).toBeInTheDocument();
  });

  it("SO 취소 409 SUCCESSOR_ALIVE(그 사이 선적이 생김) → 서버 문구 + '먼저 취소할 선적' 번호 링크", async () => {
    open(confirmed(), TRADER, [
      [
        "/v1/sales-orders/9/transitions",
        "POST",
        () =>
          jsonResponse(
            apiErrorResponse("TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE", "이어진 문서가 살아 있어 취소할 수 없습니다.", {
              successors: ["SH-2026-0001", "SH-2026-0002"],
            }),
            409,
          ),
      ],
    ]);
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "바이어 취소" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "취소 확정" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("이어진 문서가 살아 있어 취소할 수 없습니다.");
    expect(alert).toHaveTextContent("먼저 취소할 선적:");
    expect(within(alert).getByRole("link", { name: "SH-2026-0001" })).toHaveAttribute("href", "/shipments?q=SH-2026-0001");
    expect(within(alert).getByRole("link", { name: "SH-2026-0002" })).toHaveAttribute("href", "/shipments?q=SH-2026-0002");
  });
});

describe("SO 상세 — 소스 계약", () => {
  it("라인 표에 '선적 잔량' 열을 더했으니 품명 칸에 최소 폭을 둔다(390px 실브라우저 발견 — 글자 단위 꺾임)", () => {
    const src = Object.values(
      import.meta.glob("./sales-order-detail.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>,
    )[0] as string;
    expect(src).toMatch(/<th className="cell-nowrap min-w-\d+ px-3 py-2">품명<\/th>/);
  });
});

describe("선적 만들기 2단 대화상자", () => {
  function openDialog(extra: GateHandler[] = [], so = confirmed()) {
    const stub = open(so, TRADER, extra);
    return heading().then(() => {
      fireEvent.click(within(shipSection()).getByRole("button", { name: "선적 만들기" }));
      return { ...stub, dialog: screen.getByRole("dialog", { name: /선적 만들기/ }) };
    });
  }

  it("기본 수량은 빈칸·미리보기 비활성, 잔량 0 라인은 입력 불가, 가용재고 '미산정' 배지, 원천 값 입력칸 없음", async () => {
    const { dialog } = await openDialog();
    const serum = within(dialog).getByLabelText("라인 1 이번 선적 수량");
    expect(serum).toHaveValue("");
    expect(within(dialog).getByLabelText("라인 2 이번 선적 수량")).toBeDisabled();
    expect(within(dialog).getByText("남은 수량이 없습니다(이미 다른 선적에 모두 배정).")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "다음: 미리보기" })).toBeDisabled();
    expect(within(dialog).getAllByText("가용재고 미산정").length).toBe(2);
    expect(within(dialog).getByLabelText("출발국 (필수)")).toHaveValue("");
    expect(within(dialog).getByLabelText("도착국 (필수)")).toHaveValue(""); // 시장 코드(US)로 미리 채우지 않는다
    for (const absent of [/단가/, /환율/, /통화/, /인코텀즈/, /결제조건/]) {
      expect(within(dialog).queryByLabelText(absent)).toBeNull();
    }
  });

  it("[잔량 전부]·국가 입력 → 미리보기 본문은 {lines, origin, dest}만(빈칸 라인 제외) → 미리보기 표 → 생성 확정 같은 본문·키 1개 → 선적 상세로", async () => {
    const created = shipmentDetail({ id: 31 });
    const { calls, dialog } = await openDialog([
      ["/v1/sales-orders/9/shipments/preview", "POST", () => jsonResponse(shipmentPreview())],
      ["/v1/sales-orders/9/shipments", "POST", () => jsonResponse(created, 201)],
      ["/v1/shipments/31", "GET", () => jsonResponse(created)],
    ]);
    fireEvent.click(within(dialog).getByRole("button", { name: "라인 1 잔량 전부" }));
    expect(within(dialog).getByLabelText("라인 1 이번 선적 수량")).toHaveValue("6");
    fireEvent.change(within(dialog).getByLabelText("라인 1 이번 선적 수량"), { target: { value: "4" } });
    fireEvent.change(within(dialog).getByLabelText("출발국 (필수)"), { target: { value: "kr" } });
    fireEvent.change(within(dialog).getByLabelText("도착국 (필수)"), { target: { value: "US" } });
    expect(within(dialog).getByText("대한민국")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));

    expect(await within(dialog).findByText(/서버가 계산한 미리보기입니다/)).toBeInTheDocument();
    const previewBody = sent(calls, "/v1/sales-orders/9/shipments/preview", "POST")[0]!.body;
    expect(previewBody).toEqual({ lines: [{ so_line_id: 41, quantity: 4 }], origin_country_code: "KR", dest_country_code: "US" });
    expect(within(dialog).getByText("USD · 1350.5 (기준일 2026-09-29)")).toBeInTheDocument();
    expect(within(dialog).getByText("UN1950 · 2.1")).toBeInTheDocument();
    expect(within(dialog).getByText("(자동 — 수주 바이어)")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "생성 확정" }));
    fireEvent.click(within(dialog).getByRole("button", { name: /생성 확정|만드는 중/ })); // 더블클릭 — 동기 잠금
    await screen.findByRole("heading", { name: /SH-2026-0001/ });
    const creates = sent(calls, "/v1/sales-orders/9/shipments", "POST");
    expect(creates).toHaveLength(1);
    expect(creates[0]!.body).toEqual(previewBody);
    expect(creates[0]!.headers["Idempotency-Key"]).toMatch(/^key-/);
  });

  it("생성 409 EXCEEDS_OPEN(다른 선적이 먼저 가져감) → 1단으로 돌아가 그 라인 칸 아래 '서버 확인: 남은 수량 N'·'남은 잔량'도 서버 값으로 갱신, 본문이 바뀌면 새 키", async () => {
    let creates = 0;
    const { calls, dialog } = await openDialog([
      // 409 뒤 수주 재조회 — 경쟁 선적이 가져가 라인 1 잔량이 1로 줄어 있다(입력값 4는 그대로 남는다).
      [
        "/v1/sales-orders/9",
        "GET",
        () =>
          jsonResponse(
            confirmed(
              creates > 0
                ? { status: "IN_SHIPMENT", lines: [{ ...SO_LINE, shipment_open_quantity: 1 }, { ...TONER, shipment_open_quantity: 0 }] }
                : {},
            ),
          ),
      ],
      ["/v1/sales-orders/9/shipments/preview", "POST", () => jsonResponse(shipmentPreview())],
      [
        "/v1/sales-orders/9/shipments",
        "POST",
        () => {
          creates += 1;
          return jsonResponse(
            apiErrorResponse("TRADE_DOCS.QUANTITY.EXCEEDS_OPEN", "원천 남은 수량을 넘습니다.", { open_quantity: { "41": 1 } }),
            409,
          );
        },
      ],
    ]);
    fireEvent.change(within(dialog).getByLabelText("라인 1 이번 선적 수량"), { target: { value: "4" } });
    fireEvent.change(within(dialog).getByLabelText("출발국 (필수)"), { target: { value: "KR" } });
    fireEvent.change(within(dialog).getByLabelText("도착국 (필수)"), { target: { value: "US" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    const sectionGets = sent(calls, "/v1/shipments?so_id=9", "GET").length;
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));

    const input = await within(dialog).findByLabelText("라인 1 이번 선적 수량");
    // 409 = 다른 선적이 생겼다 — 이 수주의 선적 목록도 다시 받는다(적대 검토 low ⑩ — 보드·흐름도 같은 공용 함수).
    await waitFor(() => expect(sent(calls, "/v1/shipments?so_id=9", "GET").length).toBeGreaterThan(sectionGets));
    expect(input).toHaveAttribute("aria-invalid", "true");
    const hint = document.getElementById(input.getAttribute("aria-describedby") ?? "") as HTMLElement;
    expect(hint).toHaveTextContent("서버 확인: 남은 수량 1 — 수량을 1 이하로 고쳐 주세요.");
    // 상단 요약은 서버 문구 그대로(D4) — 원인을 단정하는 고정 문구 없음(적대 검토 low ⑧).
    const summary = within(dialog).getAllByRole("alert").find((el) => el.textContent?.includes("원천 남은 수량을 넘습니다.")) as HTMLElement;
    expect(summary).toHaveTextContent("라인별 남은 수량은 각 수량 칸 아래에 표시했습니다.");
    expect(within(dialog).queryByText(/다른 선적이 먼저 가져가/)).toBeNull();
    // 409로 1단에 돌아오면 첫 문제 칸에 포커스(low ⑪).
    await waitFor(() => expect(document.activeElement).toBe(input));
    const card = input.closest("li") as HTMLElement;
    await waitFor(() => expect(card).toHaveTextContent("남은 잔량 1"));
    expect(input).toHaveValue("4");

    // 고친 수량으로 다시 미리보기 → 확정: 본문이 다르므로 새 키(결과를 모르는 실패 뒤 같은 본문이면 같은 키).
    fireEvent.change(input, { target: { value: "1" } });
    expect(hint).not.toHaveTextContent("서버 확인");
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));
    await waitFor(() => expect(creates).toBe(2));
    const posts = sent(calls, "/v1/sales-orders/9/shipments", "POST");
    expect(posts[1]!.body).toEqual({ lines: [{ so_line_id: 41, quantity: 1 }], origin_country_code: "KR", dest_country_code: "US" });
    expect(posts[1]!.headers["Idempotency-Key"]).not.toBe(posts[0]!.headers["Idempotency-Key"]);
  });

  it("결과를 모르는 실패(503) → '같은 요청(같은 키)으로 결과 확인' 안내·'뒤로'·Esc 막힘 → 재확정은 같은 키(중복 선적 방지)", async () => {
    let creates = 0;
    const created = shipmentDetail({ id: 31 });
    const { calls, dialog } = await openDialog([
      ["/v1/sales-orders/9/shipments/preview", "POST", () => jsonResponse(shipmentPreview())],
      [
        "/v1/sales-orders/9/shipments",
        "POST",
        () => {
          creates += 1;
          return creates < 3
            ? jsonResponse(apiErrorResponse("COMMON.SERVER.UNAVAILABLE", "잠시 후 다시 시도해 주세요."), 503)
            : jsonResponse(created, 201);
        },
      ],
      ["/v1/shipments/31", "GET", () => jsonResponse(created)],
    ]);
    fireEvent.change(within(dialog).getByLabelText("라인 1 이번 선적 수량"), { target: { value: "2" } });
    fireEvent.change(within(dialog).getByLabelText("출발국 (필수)"), { target: { value: "KR" } });
    fireEvent.change(within(dialog).getByLabelText("도착국 (필수)"), { target: { value: "US" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("잠시 후 다시 시도해 주세요.");
    expect(alert).toHaveTextContent("같은 요청(같은 키)으로 결과를 확인합니다");
    expect(within(dialog).getByRole("button", { name: "뒤로" })).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog", { name: /선적을 만들까요/ })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /확인 없이 닫기/ })).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "생성 확정" }));
    await waitFor(() => expect(creates).toBe(2));
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));
    await screen.findByRole("heading", { name: /SH-2026-0001/ });
    const keys = sent(calls, "/v1/sales-orders/9/shipments", "POST").map((c) => c.headers["Idempotency-Key"]);
    expect(keys).toHaveLength(3);
    expect(new Set(keys).size).toBe(1);
  });

  it("결과 미확인 상태에서 '확인 없이 닫기' → 대화상자 닫힘·이 수주의 선적 목록 재조회(생성 여부를 화면에서 확인)", async () => {
    const { calls, dialog } = await openDialog([
      ["/v1/sales-orders/9/shipments/preview", "POST", () => jsonResponse(shipmentPreview())],
      ["/v1/sales-orders/9/shipments", "POST", () => jsonResponse(apiErrorResponse("X", "게이트웨이 시간 초과"), 504)],
    ]);
    fireEvent.change(within(dialog).getByLabelText("라인 1 이번 선적 수량"), { target: { value: "2" } });
    fireEvent.change(within(dialog).getByLabelText("출발국 (필수)"), { target: { value: "KR" } });
    fireEvent.change(within(dialog).getByLabelText("도착국 (필수)"), { target: { value: "US" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));
    await within(dialog).findByRole("alert");
    const before = sent(calls, "/v1/shipments?so_id=9", "GET").length;
    fireEvent.click(within(dialog).getByRole("button", { name: /확인 없이 닫기/ }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(sent(calls, "/v1/shipments?so_id=9", "GET").length).toBeGreaterThan(before));
  });

  it("409 뒤 재조회로 잔량이 모두 0이 되어도 대화상자는 닫히지 않고, 0이 된 라인은 수량을 비우고 알린다", async () => {
    let previews = 0;
    const { dialog } = await openDialog([
      [
        "/v1/sales-orders/9",
        "GET",
        () =>
          jsonResponse(
            confirmed(
              previews > 0
                ? { status: "IN_SHIPMENT", lines: [{ ...SO_LINE, shipment_open_quantity: 0 }, { ...TONER, shipment_open_quantity: 0 }] }
                : {},
            ),
          ),
      ],
      [
        "/v1/sales-orders/9/shipments/preview",
        "POST",
        () => {
          previews += 1;
          return jsonResponse(apiErrorResponse("TRADE_DOCS.QUANTITY.EXCEEDS_OPEN", "원천 남은 수량을 넘습니다.", { open_quantity: { "41": 0 } }), 409);
        },
      ],
    ]);
    fireEvent.change(within(dialog).getByLabelText("라인 1 이번 선적 수량"), { target: { value: "2" } });
    fireEvent.change(within(dialog).getByLabelText("출발국 (필수)"), { target: { value: "KR" } });
    fireEvent.change(within(dialog).getByLabelText("도착국 (필수)"), { target: { value: "US" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    expect(await within(dialog).findByText("남은 수량이 없어 이번 선적에서 뺐습니다(서버 재확인 결과).")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("라인 1 이번 선적 수량")).toHaveValue("");
    await waitFor(() => expect(within(shipSection()).queryByRole("button", { name: "선적 만들기" })).toBeNull());
    expect(screen.getByRole("dialog", { name: /선적 만들기/ })).toBeInTheDocument();
  });

  // 적대 검토 med ② — 0이 된 라인에 입력값이 남아 칸이 비활성인 채 미리보기가 영원히 막히던 막다른 길.
  it("0이 된 라인이 빠지면 남은 다른 라인만으로 미리보기가 진행된다(본문에 0 라인 없음)", async () => {
    let previews = 0;
    const { calls, dialog } = await openDialog(
      [
        [
          "/v1/sales-orders/9",
          "GET",
          () =>
            jsonResponse(
              confirmed({
                lines: [
                  { ...SO_LINE, shipment_open_quantity: previews > 0 ? 0 : 6 },
                  { ...TONER, shipment_open_quantity: 5 },
                ],
              }),
            ),
        ],
        [
          "/v1/sales-orders/9/shipments/preview",
          "POST",
          () => {
            previews += 1;
            return previews === 1
              ? jsonResponse(apiErrorResponse("TRADE_DOCS.QUANTITY.EXCEEDS_OPEN", "원천 남은 수량을 넘습니다.", { open_quantity: { "41": 0 } }), 409)
              : jsonResponse(shipmentPreview());
          },
        ],
      ],
      confirmed({ lines: [{ ...SO_LINE, shipment_open_quantity: 6 }, { ...TONER, shipment_open_quantity: 5 }] }),
    );
    fireEvent.change(within(dialog).getByLabelText("라인 1 이번 선적 수량"), { target: { value: "4" } });
    fireEvent.change(within(dialog).getByLabelText("라인 2 이번 선적 수량"), { target: { value: "2" } });
    fireEvent.change(within(dialog).getByLabelText("출발국 (필수)"), { target: { value: "KR" } });
    fireEvent.change(within(dialog).getByLabelText("도착국 (필수)"), { target: { value: "US" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    expect(await within(dialog).findByText("남은 수량이 없어 이번 선적에서 뺐습니다(서버 재확인 결과).")).toBeInTheDocument();
    // 포커스가 비활성이 된 칸과 함께 문서로 빠지지 않고 1단 제목에 있다(실브라우저 재확인 발견).
    const step1 = within(dialog).getByRole("heading", { name: /선적 만들기 — SO-2026-0001/ });
    await waitFor(() => expect(document.activeElement).toBe(step1));
    const next = within(dialog).getByRole("button", { name: "다음: 미리보기" });
    expect(next).toBeEnabled();
    fireEvent.click(next);
    expect(await within(dialog).findByRole("button", { name: "생성 확정" })).toBeInTheDocument();
    expect(sent(calls, "/v1/sales-orders/9/shipments/preview", "POST")[1]!.body).toEqual({
      lines: [{ so_line_id: 42, quantity: 2 }],
      origin_country_code: "KR",
      dest_country_code: "US",
    });
  });

  it("미리보기 409 EXCEEDS_OPEN도 칸별 잔량으로(1단 유지)", async () => {
    const { dialog } = await openDialog([
      [
        "/v1/sales-orders/9/shipments/preview",
        "POST",
        () => jsonResponse(apiErrorResponse("TRADE_DOCS.QUANTITY.EXCEEDS_OPEN", "원천 남은 수량을 넘습니다.", { open_quantity: { "41": 0 } }), 409),
      ],
    ]);
    fireEvent.change(within(dialog).getByLabelText("라인 1 이번 선적 수량"), { target: { value: "2" } });
    fireEvent.change(within(dialog).getByLabelText("출발국 (필수)"), { target: { value: "KR" } });
    fireEvent.change(within(dialog).getByLabelText("도착국 (필수)"), { target: { value: "US" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    // 서버 잔량 0이면 '0 이하로 고쳐' 대신 '비워 주세요'(적대 검토 med ②) — 재조회가 아직 잔량 6을 주므로 칸은 살아 있다.
    expect(await within(dialog).findByText("서버 확인: 남은 수량 0 — 이 라인은 비워 주세요(이번 선적에서 빼기).")).toBeInTheDocument();
  });

  it("화면 사전 검사: 보이는 잔량 초과·정수 아님·국가 누락/모르는 코드는 미리보기 요청 0", async () => {
    const { calls, dialog } = await openDialog();
    const qty = within(dialog).getByLabelText("라인 1 이번 선적 수량");
    fireEvent.change(qty, { target: { value: "7" } });
    expect(within(dialog).getByText("남은 수량(6)을 넘습니다.")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    expect(within(dialog).getAllByText("필수 입력입니다.")).toHaveLength(2); // 출발·도착 둘 다 — 시도 뒤에만 보인다
    fireEvent.change(qty, { target: { value: "1.5" } });
    expect(within(dialog).getByText("1 이상의 정수로 입력해 주세요.")).toBeInTheDocument();
    fireEvent.change(qty, { target: { value: "3" } });
    fireEvent.change(within(dialog).getByLabelText("출발국 (필수)"), { target: { value: "KR" } });
    fireEvent.change(within(dialog).getByLabelText("도착국 (필수)"), { target: { value: "EU" } }); // 시장 코드지만 국가 아님
    expect(within(dialog).getByText(/알 수 없는 국가 코드입니다/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    // CLDR 별칭 — Intl은 '영국'이라 이름을 주지만 ISO 정식 코드가 아니다(적대 검토 med ①).
    fireEvent.change(within(dialog).getByLabelText("도착국 (필수)"), { target: { value: "UK" } });
    expect(within(dialog).getByText("UK는 ISO 정식 코드가 아닙니다 — GB로 입력해 주세요.")).toBeInTheDocument();
    expect(within(dialog).queryByText("영국")).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    expect(sent(calls, "/v1/sales-orders/9/shipments/preview", "POST")).toHaveLength(0);
  });

  it("당사자(선택): 포워더는 FORWARDER 유형 검색, 고르면 본문 parties에 실린다", async () => {
    const { calls, dialog } = await openDialog([
      ["/v1/partners?type=FORWARDER&size=20", "GET", () => jsonResponse(page([{ id: 8, name_ko: "빠른포워딩", name_en: "Fast Fwd", type_codes: ["FORWARDER"] }]))],
      ["/v1/sales-orders/9/shipments/preview", "POST", () => jsonResponse(shipmentPreview())],
    ]);
    fireEvent.change(within(dialog).getByLabelText("라인 1 이번 선적 수량"), { target: { value: "1" } });
    fireEvent.change(within(dialog).getByLabelText("출발국 (필수)"), { target: { value: "KR" } });
    fireEvent.change(within(dialog).getByLabelText("도착국 (필수)"), { target: { value: "US" } });
    fireEvent.focus(within(dialog).getByRole("combobox", { name: "포워더 거래처" }));
    fireEvent.click(await within(dialog).findByRole("option", { name: "빠른포워딩 (Fast Fwd)" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    await waitFor(() => expect(sent(calls, "/v1/sales-orders/9/shipments/preview", "POST")).toHaveLength(1));
    expect(sent(calls, "/v1/sales-orders/9/shipments/preview", "POST")[0]!.body).toMatchObject({
      parties: [{ role: "FORWARDER", partner_id: 8 }],
    });
  });

  it("Esc·닫기로 닫히면 쓰기 요청 0", async () => {
    const { calls, dialog } = await openDialog();
    fireEvent.click(within(dialog).getByRole("button", { name: "닫기" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("Esc로 닫힌다(대기 중이 아닐 때)", async () => {
    await openDialog();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("생성 응답 대기 중 Esc를 눌러도 대화상자가 남는다(응답 전 닫으면 결과를 못 본다)", async () => {
    let release: (value: Response) => void = () => undefined;
    const created = shipmentDetail({ id: 31 });
    const { dialog } = await openDialog([
      ["/v1/sales-orders/9/shipments/preview", "POST", () => jsonResponse(shipmentPreview())],
      ["/v1/shipments/31", "GET", () => jsonResponse(created)],
    ]);
    const original = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) =>
        input === "/api/v1/sales-orders/9/shipments" && init?.method === "POST"
          ? new Promise<Response>((resolve) => (release = resolve))
          : original(input, init),
      ),
    );
    fireEvent.change(within(dialog).getByLabelText("라인 1 이번 선적 수량"), { target: { value: "2" } });
    fireEvent.change(within(dialog).getByLabelText("출발국 (필수)"), { target: { value: "KR" } });
    fireEvent.change(within(dialog).getByLabelText("도착국 (필수)"), { target: { value: "US" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));
    expect(await within(dialog).findByRole("button", { name: "만드는 중…" })).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog", { name: /선적을 만들까요/ })).toBeInTheDocument();
    release(jsonResponse(created, 201));
    await screen.findByRole("heading", { name: /SH-2026-0001/ });
  });

  // 적대 검토 med ③ — 미리보기 대기 중 입력을 바꾸면 늦게 온 응답이 옛 본문으로 2단을 열고, reset이 잠금을 영구히 남겼다.
  it("미리보기 대기 중 1단 입력은 잠기고, 그 사이 본문이 바뀌면 늦은 응답을 버린다 — 잠금은 풀려 다시 미리보기할 수 있다", async () => {
    const releases: Array<(value: Response) => void> = [];
    const { calls, dialog } = await openDialog();
    const original = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        if (input === "/api/v1/sales-orders/9/shipments/preview" && init?.method === "POST") {
          calls.push({ url: input, method: "POST", body: JSON.parse(String(init.body)), rawBody: init.body ?? null, headers: {} });
          return new Promise<Response>((resolve) => releases.push(resolve));
        }
        return original(input, init);
      }),
    );
    const qty = within(dialog).getByLabelText("라인 1 이번 선적 수량");
    fireEvent.change(qty, { target: { value: "2" } });
    fireEvent.change(within(dialog).getByLabelText("출발국 (필수)"), { target: { value: "KR" } });
    fireEvent.change(within(dialog).getByLabelText("도착국 (필수)"), { target: { value: "US" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    await waitFor(() => expect(within(dialog).getByLabelText("라인 1 이번 선적 수량")).toBeDisabled());
    expect(within(dialog).getByLabelText("도착국 (필수)")).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "라인 1 잔량 전부" })).toBeDisabled();

    // 잠금을 넘어선 변경(재조회 자동 비우기 등과 같은 효과를 직접 재현) — 보낸 본문(2)과 지금 본문(3)이 갈라진다.
    fireEvent.change(qty, { target: { value: "3" } });
    releases[0]!(jsonResponse(shipmentPreview()));
    expect(await within(dialog).findByText(/그 결과를 버렸습니다/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "생성 확정" })).toBeNull();

    // 잠금이 풀렸다 — 다시 미리보기 요청이 나가고(본문 3), 이번 응답은 2단을 연다.
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    await waitFor(() => expect(releases).toHaveLength(2));
    expect(sent(calls, "/v1/sales-orders/9/shipments/preview", "POST")[1]!.body).toMatchObject({ lines: [{ so_line_id: 41, quantity: 3 }] });
    releases[1]!(jsonResponse(shipmentPreview()));
    expect(await within(dialog).findByRole("button", { name: "생성 확정" })).toBeInTheDocument();
  });

  it("단계 전이: 미리보기가 열리면 2단 제목에, '뒤로'면 1단 제목에 포커스(대화상자 맨 위로)", async () => {
    const { dialog } = await openDialog([["/v1/sales-orders/9/shipments/preview", "POST", () => jsonResponse(shipmentPreview())]]);
    fireEvent.change(within(dialog).getByLabelText("라인 1 이번 선적 수량"), { target: { value: "2" } });
    fireEvent.change(within(dialog).getByLabelText("출발국 (필수)"), { target: { value: "KR" } });
    fireEvent.change(within(dialog).getByLabelText("도착국 (필수)"), { target: { value: "US" } });
    dialog.scrollTop = 500;
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    const step2 = await within(dialog).findByRole("heading", { name: /선적을 만들까요/ });
    await waitFor(() => expect(document.activeElement).toBe(step2));
    expect(dialog.scrollTop).toBe(0);
    fireEvent.click(within(dialog).getByRole("button", { name: "뒤로" }));
    const step1 = within(dialog).getByRole("heading", { name: /선적 만들기 — SO-2026-0001/ });
    await waitFor(() => expect(document.activeElement).toBe(step1));
  });
});

describe("선적 만들기 — 관리자", () => {
  it("관리자(ADMIN)에게도 '선적 만들기'가 보인다(서버 require_roles는 관리자 상시 통과)", async () => {
    open(confirmed(), ADMIN);
    await heading();
    expect(within(shipSection()).getByRole("button", { name: "선적 만들기" })).toBeInTheDocument();
  });
});
