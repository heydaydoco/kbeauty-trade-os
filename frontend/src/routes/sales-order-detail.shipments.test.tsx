// SO 상세의 선적 연동 (S3-2 PR-3b — design-D D7·D8·D4 / R-02) — '선적 잔량' 열·선적 섹션·'선적 만들기' 노출 조건·2단 대화상자
// (빈칸 기본·[잔량 전부]·국가 필수·미리보기 본문·생성 키·409 칸별 잔량)·선적중 안내·SO 취소 409 '먼저 취소할 선적' 링크. 그룹 A·J.
// fetch 스텁은 정확 URL·메서드 일치(stubGateFetch) — 본문·경로 오류는 404로 터진다.

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
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
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));

    const input = await within(dialog).findByLabelText("라인 1 이번 선적 수량");
    expect(input).toHaveAttribute("aria-invalid", "true");
    const hint = document.getElementById(input.getAttribute("aria-describedby") ?? "") as HTMLElement;
    expect(hint).toHaveTextContent("서버 확인: 남은 수량 1 — 수량을 1 이하로 고쳐 주세요.");
    expect(within(dialog).getByText(/다른 선적이 먼저 가져가 남은 수량이 줄었습니다/)).toBeInTheDocument();
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

  it("결과를 모르는 실패(503) 뒤 같은 본문 재확정은 같은 키 — 중복 선적 방지(뒤로 갔다 같은 값으로 다시 와도 같은 키)", async () => {
    const { calls, dialog } = await openDialog([
      ["/v1/sales-orders/9/shipments/preview", "POST", () => jsonResponse(shipmentPreview())],
      ["/v1/sales-orders/9/shipments", "POST", () => jsonResponse(apiErrorResponse("COMMON.SERVER.UNAVAILABLE", "잠시 후 다시 시도해 주세요."), 503)],
    ]);
    fireEvent.change(within(dialog).getByLabelText("라인 1 이번 선적 수량"), { target: { value: "2" } });
    fireEvent.change(within(dialog).getByLabelText("출발국 (필수)"), { target: { value: "KR" } });
    fireEvent.change(within(dialog).getByLabelText("도착국 (필수)"), { target: { value: "US" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("잠시 후 다시 시도해 주세요.");
    fireEvent.click(within(dialog).getByRole("button", { name: "생성 확정" }));
    await waitFor(() => expect(sent(calls, "/v1/sales-orders/9/shipments", "POST")).toHaveLength(2));
    fireEvent.click(within(dialog).getByRole("button", { name: "뒤로" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 미리보기" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "생성 확정" }));
    await waitFor(() => expect(sent(calls, "/v1/sales-orders/9/shipments", "POST")).toHaveLength(3));
    const keys = sent(calls, "/v1/sales-orders/9/shipments", "POST").map((c) => c.headers["Idempotency-Key"]);
    expect(new Set(keys).size).toBe(1);
  });

  it("409 뒤 재조회로 잔량이 모두 0이 되어도 대화상자는 닫히지 않고 칸별 안내가 남는다", async () => {
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
    expect(await within(dialog).findByText("서버 확인: 남은 수량 0 — 수량을 0 이하로 고쳐 주세요.")).toBeInTheDocument();
    await waitFor(() => expect(within(shipSection()).queryByRole("button", { name: "선적 만들기" })).toBeNull());
    expect(screen.getByRole("dialog", { name: /선적 만들기/ })).toBeInTheDocument();
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
    expect(await within(dialog).findByText("서버 확인: 남은 수량 0 — 수량을 0 이하로 고쳐 주세요.")).toBeInTheDocument();
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
});
