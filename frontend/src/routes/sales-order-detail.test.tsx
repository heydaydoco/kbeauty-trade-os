// SO 상세 — 상태별 버튼·동결 읽기 전용·헤더/라인 편집(바뀐 필드만·무변경 차단)·중복 PO 409·낙관 잠금·보류/재개/취소 다이얼로그 (S3-1 PR-7b).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { stubFetch, type Call } from "../test/qt-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";
import { PRICE_BLOCK, report } from "../test/gate-fixtures";
import { SO_LINE, SO_LOG, chainFlow, soDetail } from "../test/so-fixtures";

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const server = { so: soDetail() };

const REFS: Array<[string, string, () => Response]> = [
  ["/v1/sales-orders/9/status-log", "GET", () => jsonResponse(SO_LOG)],
  ["/v1/document-flow/SALES_ORDER/9", "GET", () => jsonResponse(chainFlow("SALES_ORDER"))],
  ["/v1/users/lookup", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }]))],
  ["/v1/markets", "GET", () => jsonResponse(page([{ id: 1, code: "US", name_ko: "미국", name_en: null, note: null, version: 1 }]))],
];

function open(
  so: ReturnType<typeof soDetail>,
  extra: Array<[string, string, () => Response]> = [],
  me: unknown = TRADER,
) {
  server.so = so;
  const stub = stubFetch(me, [...extra, ...REFS, ["/v1/sales-orders/9", "GET", () => jsonResponse(server.so)]]);
  renderWithProviders(<AppRoutes />, { route: "/sales-orders/9" });
  return stub;
}

const CONFLICT = () =>
  jsonResponse({ error: { code: "COMMON.CONCURRENCY.VERSION_CONFLICT", message: "다른 사용자가 먼저 수정했습니다." } }, 409);
const DUPLICATE_PO = () =>
  jsonResponse(
    {
      error: {
        code: "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO",
        message: "같은 바이어의 같은 PO번호가 이미 다른 수주에 등록되어 있습니다.",
        detail: { doc_number: "SO-2026-0007", status: "RECEIVED" },
      },
    },
    409,
  );

const heading = () => screen.findByRole("heading", { name: /SO-2026-0001/ });
const sent = (calls: Call[], suffix: string, method: string) =>
  calls.filter((c) => c.method === method && c.url.endsWith(suffix));

describe("SO 상세 — 표시", () => {
  it("헤더·서버 계산 금액·원천 대비·타임라인·문서 흐름을 서버 값 그대로 보인다", async () => {
    // 확정 상태 = 읽기 전용 헤더(값이 입력칸이 아니라 글자로 보인다).
    open(soDetail({ status: "CONFIRMED", confirmed_at: "2026-09-30T02:00:00Z", lines: [{ ...SO_LINE, quantity: 5, quantity_delta: -1, price_changed: true, unit_price_text: "11.00", line_amount_text: "55.00" }], total_text: "55.00" }));
    await heading();
    expect(screen.getByText("선수금 T/T · 선수금 30% · 잔금 B/L일 기준 30일")).toBeInTheDocument();
    expect(screen.getByText("PO-2026-001")).toBeInTheDocument();
    expect(screen.getByText("55.00 USD")).toBeInTheDocument();
    expect(screen.getByText("원천 수량 6 (-1)")).toBeInTheDocument();
    expect(screen.getByText("원천 단가 12.50 — 변경됨")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "QT-2026-0001" })[0]).toHaveAttribute("href", "/quotations/7"); // 헤더 원천 링크 + 흐름 패널
    expect(await screen.findByText("접수 (작성)")).toBeInTheDocument(); // 타임라인 재사용
    // 문서 흐름 패널: 현재 SO는 링크가 아니고 QT·PI는 링크.
    expect(await screen.findByText("현재 문서")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "PI-2026-0001" }).length).toBeGreaterThan(0);
  });

  it("단종 SKU 라인은 배지로 알린다(접수는 저장 허용·확정에서 차단)", async () => {
    open(soDetail({ lines: [{ ...SO_LINE, sku_status: "DISCONTINUED" }] }));
    await heading();
    expect(screen.getByText("단종")).toBeInTheDocument();
  });

  it("없는 수주는 404 문구와 목록 링크", async () => {
    stubFetch(TRADER, [["/v1/sales-orders/9", "GET", () => jsonResponse({ error: { code: "NOT_FOUND", message: "x" } }, 404)]]);
    renderWithProviders(<AppRoutes />, { route: "/sales-orders/9" });
    expect(await screen.findByRole("alert")).toHaveTextContent("수주를 찾을 수 없습니다.");
    expect(screen.getByRole("link", { name: "수주 목록으로" })).toBeInTheDocument();
  });

  it("조회 전용 역할은 버튼·편집 폼이 없고 메모는 글로만 보인다", async () => {
    open(soDetail({ internal_note: "내부 참고" }), [], VIEWER);
    await heading();
    for (const name of ["보류", "재개", "취소"]) expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "수주 헤더 편집" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "라인 추가" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "내부 메모·담당자" })).not.toBeInTheDocument();
    expect(screen.getByText("내부 메모: 내부 참고")).toBeInTheDocument();
  });
});

describe("SO 상세 — 상태별 버튼과 편집 가능 여부", () => {
  it.each([
    ["RECEIVED", null, { hold: true, resume: false, cancel: true, edit: true }],
    ["CONFIRMED", "2026-09-30T02:00:00Z", { hold: true, resume: false, cancel: true, edit: false }],
    ["ON_HOLD", null, { hold: false, resume: true, cancel: true, edit: false }],
    ["CANCELLED", null, { hold: false, resume: false, cancel: false, edit: false }],
  ])("%s", async (status, confirmedAt, want) => {
    open(soDetail({ status, confirmed_at: confirmedAt }));
    await heading();
    expect(screen.queryByRole("button", { name: "보류" }) !== null).toBe(want.hold);
    expect(screen.queryByRole("button", { name: "재개" }) !== null).toBe(want.resume);
    expect(screen.queryByRole("button", { name: "취소" }) !== null).toBe(want.cancel);
    expect(screen.queryByRole("form", { name: "수주 헤더 편집" }) !== null).toBe(want.edit);
    expect(screen.queryByRole("form", { name: "라인 추가" }) !== null).toBe(want.edit);
    expect(screen.queryByRole("button", { name: "수정" }) !== null).toBe(want.edit);
    expect(screen.queryByRole("button", { name: "제외" }) !== null).toBe(want.edit);
    // FREE 열은 동결·보류·취소 뒤에도 열려 있다.
    expect(screen.getByRole("form", { name: "내부 메모·담당자" })).toBeInTheDocument();
  });

  it("편집 중이던 폼은 보류되면 닫힌다(읽기 전용으로 전환)", async () => {
    const held = soDetail({ status: "ON_HOLD", version: 4 });
    open(soDetail(), [["/v1/sales-orders/9/transitions", "POST", () => jsonResponse((server.so = held))]]);
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    expect(screen.getByRole("form", { name: "라인 1 수정" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "보류" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("보류 사유 (필수)"), { target: { value: "바이어 요청" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "보류 확정" }));
    await waitFor(() => expect(screen.queryByRole("form", { name: "라인 1 수정" })).not.toBeInTheDocument());
    expect(screen.queryByRole("form", { name: "수주 헤더 편집" })).not.toBeInTheDocument();
    expect(await screen.findByText(/보류 중입니다/)).toBeInTheDocument();
  });
});

describe("SO 상세 — 헤더 편집", () => {
  it("무변경 저장은 막고, 바뀐 필드만 기준 version과 함께 보낸다", async () => {
    const { calls } = open(soDetail(), [
      ["/v1/sales-orders/9", "PATCH", () => jsonResponse((server.so = soDetail({ buyer_po_no: "PO-NEW", version: 4 })))],
    ]);
    await heading();
    const save = screen.getByRole("button", { name: "헤더 저장" });
    expect(save).toBeDisabled();
    expect(screen.getByText("바뀐 내용이 없습니다.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("바이어 PO번호"), { target: { value: " PO-NEW " } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(sent(calls, "/v1/sales-orders/9", "PATCH")).toHaveLength(1));
    expect(sent(calls, "/v1/sales-orders/9", "PATCH")[0]?.body).toEqual({ version: 3, buyer_po_no: "PO-NEW" });
    await waitFor(() => expect(screen.getByRole("button", { name: "헤더 저장" })).toBeDisabled());
  });

  it("증빙일을 비우면 저장할 수 없고 필수 안내가 뜬다", async () => {
    open(soDetail());
    await heading();
    fireEvent.change(screen.getByLabelText("증빙일 (필수)"), { target: { value: "" } });
    expect(screen.getByRole("button", { name: "헤더 저장" })).toBeDisabled();
    expect(screen.getByText("증빙일은 필수입니다.")).toBeInTheDocument();
  });

  it("바이어 PO 일자가 미래면 화면에서 막는다", async () => {
    const { calls } = open(soDetail());
    await heading();
    fireEvent.change(screen.getByLabelText("바이어 PO 일자"), { target: { value: "2999-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "헤더 저장" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("바이어 PO 일자는 오늘보다 미래일 수 없습니다.");
    expect(sent(calls, "/v1/sales-orders/9", "PATCH")).toHaveLength(0);
  });

  it("중복 바이어 PO(409)는 서버 문구와 점유 수주를 안내한다", async () => {
    open(soDetail(), [["/v1/sales-orders/9", "PATCH", DUPLICATE_PO]]);
    await heading();
    fireEvent.change(screen.getByLabelText("바이어 PO번호"), { target: { value: "PO-DUP" } });
    fireEvent.click(screen.getByRole("button", { name: "헤더 저장" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("같은 바이어의 같은 PO번호가 이미 다른 수주에 등록되어 있습니다.");
    expect(alert).toHaveTextContent("이미 등록된 수주: SO-2026-0007 (접수)");
  });

  it("참조 수주는 목적지를 바꿀 수 없다(직접 수주는 가능)", async () => {
    open(soDetail({ is_reference: true }));
    await heading();
    expect(screen.getByLabelText(/목적지 시장/)).toBeDisabled();
  });

  it("직접 수주는 목적지를 고르면 그 필드만 보낸다", async () => {
    const { calls } = open(soDetail({ is_reference: false, qt_id: null, pi_id: null, qt_doc_number: null, pi_doc_number: null, lines: [{ ...SO_LINE, source: null, quantity_delta: null, price_changed: null }] }), [
      ["/v1/sales-orders/9", "PATCH", () => jsonResponse((server.so = soDetail({ version: 4 })))],
      ["/v1/markets", "GET", () => jsonResponse(page([{ id: 1, code: "US", name_ko: "미국", name_en: null, note: null, version: 1 }, { id: 2, code: "JP", name_ko: "일본", name_en: null, note: null, version: 1 }]))],
    ]);
    await heading();
    await screen.findByRole("option", { name: "일본 (JP)" });
    fireEvent.change(screen.getByLabelText("목적지 시장"), { target: { value: "JP" } });
    fireEvent.click(screen.getByRole("button", { name: "헤더 저장" }));
    await waitFor(() => expect(sent(calls, "/v1/sales-orders/9", "PATCH")).toHaveLength(1));
    expect(sent(calls, "/v1/sales-orders/9", "PATCH")[0]?.body).toEqual({ version: 3, dest_market_code: "JP" });
  });
});

describe("SO 상세 — 결제조건 입력 검증", () => {
  it.each([
    ["잔금 일수", "abc", /잔금 일수는 정수로/],
    ["잔금 일수", "30.5", /잔금 일수는 정수로/],
    ["잔금 일수", "-5", /음수 잔금 일수는/],
    ["잔금 일수", "400", /-90~365/],
    ["선수금 비율(%)", "abc", /선수금 비율은 0.01~100/],
    ["선수금 비율(%)", "30.123", /선수금 비율은 0.01~100/],
    ["선수금 비율(%)", "0", /선수금 비율은 0.01~100/],
    ["선수금 비율(%)", "101", /선수금 비율은 0.01~100/],
  ])("%s=%s 이면 저장을 막고 안내한다", async (label, value, message) => {
    const { calls } = open(soDetail());
    await heading();
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
    expect(screen.getByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("button", { name: "헤더 저장" })).toBeDisabled();
    fireEvent.submit(screen.getByRole("form", { name: "수주 헤더 편집" }));
    expect(sent(calls, "/v1/sales-orders/9", "PATCH")).toHaveLength(0);
  });

  it("빈 값은 의도적 null로 보내고, 정상 값(30.5%·-5일+선적예정일)은 통과한다", async () => {
    const { calls } = open(soDetail(), [
      ["/v1/sales-orders/9", "PATCH", () => jsonResponse((server.so = soDetail({ version: 4 })))],
    ]);
    await heading();
    fireEvent.change(screen.getByLabelText("잔금 일수"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("선수금 비율(%)"), { target: { value: "30.5" } });
    fireEvent.click(screen.getByRole("button", { name: "헤더 저장" }));
    await waitFor(() => expect(sent(calls, "/v1/sales-orders/9", "PATCH")).toHaveLength(1));
    expect(sent(calls, "/v1/sales-orders/9", "PATCH")[0]?.body).toEqual({
      version: 3,
      payment_terms: { payment_type: "TT_ADVANCE", advance_pct: "30.5", balance_anchor: "BL_DATE", balance_days: null },
    });
  });

  it("음수 일수는 선적예정일 기준이면 허용한다", async () => {
    open(soDetail());
    await heading();
    fireEvent.change(screen.getByLabelText("잔금 기준"), { target: { value: "ETD_DATE" } });
    fireEvent.change(screen.getByLabelText("잔금 일수"), { target: { value: "-5" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "헤더 저장" })).toBeEnabled();
  });
});

describe("SO 상세 — 오류 표시는 한 곳", () => {
  it("라인 수정 실패(비충돌)는 폼 안 경고 한 곳에만 뜨고 페이지 배너는 없다", async () => {
    open(soDetail(), [
      ["/v1/sales-orders/9/lines/41", "PATCH", () => jsonResponse({ error: { code: "X", message: "라인을 저장하지 못했습니다." } }, 422)],
    ]);
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    const form = screen.getByRole("form", { name: "라인 1 수정" });
    fireEvent.change(within(form).getByLabelText("수량"), { target: { value: "2" } });
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));
    await screen.findByText("라인을 저장하지 못했습니다.");
    expect(screen.getAllByText("라인을 저장하지 못했습니다.")).toHaveLength(1);
  });

  it("재개 목표 불일치(409)는 영문 코드 없이 한국어 안내와 최신 불러오기를 보인다", async () => {
    open(soDetail({ status: "ON_HOLD" }), [
      [
        "/v1/sales-orders/9/transitions",
        "POST",
        () => jsonResponse({ error: { code: "TRADE_DOCS.RESUME.TARGET_MISMATCH", message: "서버 문구", detail: { expected: "CONFIRMED" } } }, 409),
      ],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "재개" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "재개" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("보류 직전 상태가 바뀌었습니다");
    expect(alert).not.toHaveTextContent("CONFIRMED");
  });
});

describe("SO 상세 — 낙관 잠금", () => {
  it("저장이 409면 '다른 곳에서 수정됨' 안내와 최신 불러오기를 보이고, 불러오면 새 version으로 보낸다", async () => {
    let conflict = true;
    const { calls } = open(soDetail(), [
      ["/v1/sales-orders/9", "PATCH", () => (conflict ? CONFLICT() : jsonResponse((server.so = soDetail({ buyer_po_no: "PO-X", version: 6 }))))],
    ]);
    await heading();
    fireEvent.change(screen.getByLabelText("바이어 PO번호"), { target: { value: "PO-X" } });
    fireEvent.click(screen.getByRole("button", { name: "헤더 저장" }));
    expect(await screen.findByText(/다른 곳에서 이 수주 정보가 먼저 수정되었습니다/)).toBeInTheDocument();
    // 서버 쪽 version이 앞서갔다고 가정하고 최신 불러오기.
    server.so = soDetail({ version: 5, buyer_po_no: "PO-SERVER" });
    fireEvent.click(screen.getAllByRole("button", { name: "최신 내용 불러오기" })[0]!);
    await waitFor(() => expect(screen.getByLabelText("바이어 PO번호")).toHaveValue("PO-SERVER"));
    conflict = false;
    fireEvent.change(screen.getByLabelText("바이어 PO번호"), { target: { value: "PO-X" } });
    fireEvent.click(screen.getByRole("button", { name: "헤더 저장" }));
    await waitFor(() => expect(sent(calls, "/v1/sales-orders/9", "PATCH")).toHaveLength(2));
    expect(sent(calls, "/v1/sales-orders/9", "PATCH")[1]?.body).toEqual({ version: 5, buyer_po_no: "PO-X" });
  });

  it("서버 version이 앞서가면(창 재조회) 배너를 보이고 쓰기는 화면이 본 version으로 보낸다", async () => {
    const { calls } = open(soDetail(), [["/v1/sales-orders/9/meta", "PATCH", CONFLICT]]);
    await heading();
    server.so = soDetail({ version: 7 });
    window.dispatchEvent(new Event("visibilitychange"));
    expect(await screen.findByText(/다른 곳에서 이 수주가 수정되었습니다/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("내부 메모"), { target: { value: "메모" } });
    fireEvent.click(screen.getByRole("button", { name: "메모·담당자 저장" }));
    await waitFor(() => expect(sent(calls, "/v1/sales-orders/9/meta", "PATCH")).toHaveLength(1));
    expect(sent(calls, "/v1/sales-orders/9/meta", "PATCH")[0]?.body).toMatchObject({ version: 3 });
  });
});

describe("SO 상세 — 내부 메모·담당자(FREE 열)", () => {
  it("동결(확정) 뒤에도 고치며, 무변경 저장은 막는다", async () => {
    const { calls } = open(soDetail({ status: "CONFIRMED", confirmed_at: "2026-09-30T02:00:00Z" }), [
      ["/v1/sales-orders/9/meta", "PATCH", () => jsonResponse((server.so = soDetail({ status: "CONFIRMED", internal_note: "인계", version: 4 })))],
    ]);
    await heading();
    const save = screen.getByRole("button", { name: "메모·담당자 저장" });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("내부 메모"), { target: { value: " 인계 " } });
    fireEvent.click(save);
    await waitFor(() => expect(sent(calls, "/v1/sales-orders/9/meta", "PATCH")).toHaveLength(1));
    expect(sent(calls, "/v1/sales-orders/9/meta", "PATCH")[0]?.body).toEqual({ version: 3, internal_note: "인계", assignee_id: 1 });
  });
});

describe("SO 상세 — 라인 편집", () => {
  it("수정: 바뀐 필드만 보내고 무변경은 막으며 응답의 헤더 version·합계를 즉시 반영한다", async () => {
    const { calls } = open(soDetail(), [
      [
        "/v1/sales-orders/9/lines/41",
        "PATCH",
        () => jsonResponse({ line: { ...SO_LINE, quantity: 4 }, header_version: 4, total_amount: 5000, total_text: "50.00" }),
      ],
    ]);
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    const form = screen.getByRole("form", { name: "라인 1 수정" });
    expect(within(form).getByRole("button", { name: "저장" })).toBeDisabled();
    fireEvent.change(within(form).getByLabelText("수량"), { target: { value: "0" } });
    expect(within(form).getByRole("button", { name: "저장" })).toBeDisabled();
    expect(screen.getByText("수량은 1 이상의 정수로 입력해 주세요.")).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText("수량"), { target: { value: "4" } });
    fireEvent.change(within(form).getByLabelText("요청납기"), { target: { value: "2026-11-15" } });
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(sent(calls, "/v1/sales-orders/9/lines/41", "PATCH")).toHaveLength(1));
    // 단가를 안 바꿨으니 unit_price(=기준 MANUAL 전환)를 보내지 않는다.
    expect(sent(calls, "/v1/sales-orders/9/lines/41", "PATCH")[0]?.body).toEqual({
      version: 3,
      quantity: 4,
      requested_delivery_date: "2026-11-15",
    });
    await waitFor(() => expect(screen.queryByRole("form", { name: "라인 1 수정" })).not.toBeInTheDocument());
  });

  it("잔량 초과(409)는 서버 문구와 원천 남은 수량을 보인다", async () => {
    open(soDetail(), [
      [
        "/v1/sales-orders/9/lines/41",
        "PATCH",
        () =>
          jsonResponse(
            {
              error: {
                code: "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN",
                message: "가져올 수량이 남은 수량을 넘었습니다.",
                detail: { open_quantity: { "31": 2 } },
              },
            },
            409,
          ),
      ],
    ]);
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    const form = screen.getByRole("form", { name: "라인 1 수정" });
    fireEvent.change(within(form).getByLabelText("수량"), { target: { value: "99" } });
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));
    expect(await screen.findByText(/가져올 수량이 남은 수량을 넘었습니다\. — 원천 남은 수량 2/)).toBeInTheDocument();
  });

  it("제외: 확인 후 version을 쿼리로 보내고 합계를 반영한다", async () => {
    const { calls } = open(soDetail(), [
      ["/v1/sales-orders/9/lines/41", "DELETE", () => jsonResponse({ line: null, header_version: 4, total_amount: 0, total_text: "0.00" })],
    ]);
    await heading();
    fireEvent.click(screen.getByRole("button", { name: "제외" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "제외" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/v1/sales-orders/9/lines/41?version=3"))).toBe(true));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("추가(참조 수주): 가격 필드 없이 SKU·수량만 보낸다", async () => {
    const { calls } = open(soDetail(), [
      ["/v1/skus", "GET", () => jsonResponse(page([{ id: 8, sku_code: "SKU-008", name_ko: "톤업 크림", name_en: null, status: "ACTIVE", kind: "SINGLE" }]))],
      ["/v1/sales-orders/9/lines", "POST", () => jsonResponse({ line: { ...SO_LINE, id: 42, line_no: 2 }, header_version: 4, total_amount: 15000, total_text: "150.00" }, 201)],
    ]);
    await heading();
    const form = screen.getByRole("form", { name: "라인 추가" });
    expect(within(form).queryByLabelText(/단가/)).not.toBeInTheDocument();
    fireEvent.change(within(form).getByRole("combobox"), { target: { value: "SKU" } });
    fireEvent.click(await within(form).findByRole("option", { name: /SKU-008/ }));
    fireEvent.change(within(form).getByLabelText("수량"), { target: { value: "3" } });
    fireEvent.click(within(form).getByRole("button", { name: "라인 추가" }));
    await waitFor(() => expect(sent(calls, "/v1/sales-orders/9/lines", "POST")).toHaveLength(1));
    expect(sent(calls, "/v1/sales-orders/9/lines", "POST")[0]?.body).toEqual({ version: 3, sku_id: 8, quantity: 3 });
  });
});

describe("SO 상세 — 보류·재개·취소 다이얼로그", () => {
  it("보류: 사유가 비면 확정할 수 없고, 사유·기준 version·멱등 키와 함께 transitions로 간다", async () => {
    const { calls } = open(soDetail(), [
      ["/v1/sales-orders/9/transitions", "POST", () => jsonResponse((server.so = soDetail({ status: "ON_HOLD", version: 4 })))],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "보류" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "보류 확정" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("보류 사유 (필수)"), { target: { value: "  바이어 요청  " } });
    fireEvent.click(confirm);
    await waitFor(() => expect(sent(calls, "/v1/sales-orders/9/transitions", "POST")).toHaveLength(1));
    const post = sent(calls, "/v1/sales-orders/9/transitions", "POST")[0];
    expect(post?.body).toEqual({ to: "ON_HOLD", version: 3, reason: "바이어 요청" });
    expect(post?.headers["Idempotency-Key"]).toBe("key-1");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByRole("button", { name: "재개" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "보류" })).not.toBeInTheDocument();
  });

  it.each([
    ["접수 중 보류였던 수주", null, "RECEIVED"],
    ["확정 이력이 있는 수주", "2026-09-30T02:00:00Z", "CONFIRMED"],
  ])("재개: %s는 보류 직전 상태(%s)로 돌아가는 목표를 보낸다(사유 없이)", async (_label, confirmedAt, target) => {
    const { calls } = open(soDetail({ status: "ON_HOLD", confirmed_at: confirmedAt }), [
      ["/v1/sales-orders/9/transitions", "POST", () => jsonResponse((server.so = soDetail({ status: target, confirmed_at: confirmedAt, version: 4 })))],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "재개" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("textbox")).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "재개" }));
    await waitFor(() => expect(sent(calls, "/v1/sales-orders/9/transitions", "POST")).toHaveLength(1));
    expect(sent(calls, "/v1/sales-orders/9/transitions", "POST")[0]?.body).toEqual({ to: target, version: 3, reason: null });
  });

  it("취소: 되돌릴 수 없음을 알리고 사유 필수, 성공하면 읽기 전용이 된다", async () => {
    const { calls } = open(soDetail(), [
      ["/v1/sales-orders/9/transitions", "POST", () => jsonResponse((server.so = soDetail({ status: "CANCELLED", version: 4 })))],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "취소" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/되돌릴 수 없습니다/)).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "정정" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "취소 확정" }));
    await waitFor(() => expect(sent(calls, "/v1/sales-orders/9/transitions", "POST")[0]?.body).toEqual({ to: "CANCELLED", version: 3, reason: "정정" }));
    await waitFor(() => expect(screen.queryByRole("form", { name: "수주 헤더 편집" })).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "취소" })).not.toBeInTheDocument();
    expect(await screen.findByText(/취소된 수주입니다/)).toBeInTheDocument();
  });

  it("더블클릭은 1회만 전송한다(동기 잠금)", async () => {
    let release!: (value: Response) => void;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    const { calls } = open(soDetail());
    const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal(
      "fetch",
      vi.fn((u: string, i?: RequestInit) => {
        if (u.endsWith("/transitions")) {
          calls.push({ url: u, method: "POST", body: null, headers: {} });
          return pending;
        }
        return base(u, i);
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "취소" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "사유" } });
    const confirm = within(dialog).getByRole("button", { name: "취소 확정" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/transitions"))).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(calls.filter((c) => c.url.endsWith("/transitions"))).toHaveLength(1);
    release(jsonResponse(soDetail({ status: "CANCELLED", version: 4 })));
  });

  it("같은 본문 재시도는 같은 멱등 키, 사유가 바뀌면 새 키, 다이얼로그를 다시 열면 새 키", async () => {
    const { calls } = open(soDetail(), [["/v1/sales-orders/9/transitions", "POST", () => jsonResponse({ error: { code: "X", message: "실패" } }, 500)]]);
    fireEvent.click(await screen.findByRole("button", { name: "취소" }));
    let dialog = await screen.findByRole("dialog");
    const send = async (n: number) => {
      fireEvent.click(within(dialog).getByRole("button", { name: "취소 확정" }));
      await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/transitions"))).toHaveLength(n));
      await within(dialog).findByRole("alert");
      await waitFor(() => expect(within(dialog).getByRole("button", { name: "취소 확정" })).toBeEnabled());
    };
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "사유 A" } });
    await send(1);
    await send(2);
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "사유 B" } });
    await send(3);
    fireEvent.click(within(dialog).getByRole("button", { name: "닫기" }));
    fireEvent.click(await screen.findByRole("button", { name: "취소" }));
    dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "사유 B" } });
    await send(4);
    const keys = calls.filter((c) => c.url.endsWith("/transitions")).map((c) => c.headers["Idempotency-Key"]);
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
    expect(keys[3]).not.toBe(keys[2]);
  });

  it("전이 409(낙관 잠금)는 다이얼로그 안에서 최신 불러오기를 보인다", async () => {
    open(soDetail(), [["/v1/sales-orders/9/transitions", "POST", CONFLICT]]);
    fireEvent.click(await screen.findByRole("button", { name: "보류" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("보류 사유 (필수)"), { target: { value: "사유" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "보류 확정" }));
    expect(await within(dialog).findByText(/다른 곳에서 이 수주 정보가 먼저 수정되었습니다/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });

  it("Esc로 닫히고 포커스가 연 버튼으로 돌아온다", async () => {
    open(soDetail());
    const button = await screen.findByRole("button", { name: "보류" });
    button.focus();
    fireEvent.click(button);
    await screen.findByRole("dialog");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(document.activeElement).toBe(button);
  });
});

describe("SO 상세 — 게이트 패널(PR-11b)", () => {
  it("게이트 판정 패널이 보이고 SO 상태를 넘긴다(확정 SO는 override 버튼 없음)", async () => {
    open(soDetail({ status: "CONFIRMED", confirmed_at: "2026-09-30T02:00:00Z" }), [["/v1/sales-orders/9/gates", "GET", () => jsonResponse(report([PRICE_BLOCK], { status: "CONFIRMED" }))]]);
    await heading();
    expect(await screen.findByRole("heading", { name: "게이트 판정" })).toBeInTheDocument();
    expect(await screen.findByText("기준가 대비 편차가 허용치를 넘습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /예외 승인/ })).not.toBeInTheDocument();
  });

  it("접수 SO에서 can_override 항목에 버튼이 보이고 다이얼로그가 열린다", async () => {
    open(soDetail(), [["/v1/sales-orders/9/gates", "GET", () => jsonResponse(report([PRICE_BLOCK]))]]);
    fireEvent.click(await screen.findByRole("button", { name: "가격 편차 · 라인 1 예외 승인(override)" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("예외 승인(override)을 부여할까요?");
  });

  it("override 부여 성공 뒤 SO 상세도 다시 읽는다(무효화)", async () => {
    const { calls } = open(soDetail(), [
      ["/v1/sales-orders/9/gates", "GET", () => jsonResponse(report([PRICE_BLOCK]))],
      ["/v1/sales-orders/9/gate-overrides", "POST", () => jsonResponse({ id: 1 }, 201)],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "가격 편차 · 라인 1 예외 승인(override)" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "바이어와 합의한 가격입니다" } });
    const detailGets = () => calls.filter((c) => c.method === "GET" && c.url.endsWith("/v1/sales-orders/9")).length;
    const before = detailGets();
    fireEvent.click(within(dialog).getByRole("button", { name: "예외 승인 부여" }));
    await waitFor(() => expect(detailGets()).toBeGreaterThan(before));
  });
});
