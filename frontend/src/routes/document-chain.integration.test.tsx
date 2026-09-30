// DoD ① 통합: QT 상세 → 'PI 만들기' → PI 상세 → 'SO 만들기' → SO 상세를 화면 이동만으로 재입력 없이 관통한다 (S3-1 PR-7b).
//
// 사용자가 넣는 것은 입금 은행·유효기간(PI)과 바이어 PO(SO)뿐이다. 각 단계 요청 본문에는 SKU·단가·통화·환율·거래처가 없어야 하고
// (원천에서 서버가 복사한다), 마지막 SO 상세의 문서 흐름 패널에 QT→PI→SO 계보가 이어져 보여야 한다.

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { PI_LOG, bankAccount, piDetail, piPreview } from "../test/pi-fixtures";
import { LOG, detail, stubFetch } from "../test/qt-fixtures";
import { TRADER, jsonResponse, page, renderWithProviders } from "../test/render";
import { SO_LOG, chainFlow, soDetail } from "../test/so-fixtures";

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** 원천 값 — 본문에 다시 나타나면 '재입력'이다. */
const ORIGIN_KEYS = [
  "sku_id",
  "sku_code",
  "unit_price",
  "unit_price_amount",
  "unit_price_text",
  "currency",
  "fx_rate",
  "fx_rate_date",
  "buyer_partner_id",
  "buyer_name",
  "dest_market_code",
  "payment_terms",
  "incoterm",
  "qt_id",
  "pi_id",
];

describe("DoD ① — QT → PI → SO 관통(재입력 없음)", () => {
  it("화면 이동만으로 QT 상세에서 SO 상세까지 가고, 각 요청 본문에 원천 값이 없으며, 문서 흐름에 계보가 보인다", async () => {
    const state = { piCreated: false, soCreated: false };
    const qt = detail({ status: "ISSUED", frozen_at: "2026-09-30T02:00:00Z" });
    const { calls } = stubFetch(TRADER, [
      ["/v1/quotations/7/proforma-invoices/preview", "POST", () => jsonResponse(piPreview())],
      [
        "/v1/quotations/7/proforma-invoices",
        "POST",
        () => {
          state.piCreated = true;
          return jsonResponse(piDetail(), 201);
        },
      ],
      [
        "/v1/proforma-invoices/5/sales-orders",
        "POST",
        () => {
          state.soCreated = true;
          return jsonResponse(soDetail(), 201);
        },
      ],
      ["/v1/bank-accounts", "GET", () => jsonResponse(page([bankAccount()]))],
      ["/v1/quotations/7/status-log", "GET", () => jsonResponse(LOG)],
      ["/v1/proforma-invoices/5/status-log", "GET", () => jsonResponse(PI_LOG)],
      ["/v1/sales-orders/9/status-log", "GET", () => jsonResponse(SO_LOG)],
      [
        "/v1/document-flow/",
        "GET",
        () => {
          const full = chainFlow("SALES_ORDER", state.soCreated);
          if (!state.piCreated) full.nodes = full.nodes.slice(0, 1);
          return jsonResponse(full);
        },
      ],
      ["/v1/sales-orders/9", "GET", () => jsonResponse(soDetail())],
      ["/v1/proforma-invoices/5", "GET", () => jsonResponse(piDetail())],
      ["/v1/quotations/7", "GET", () => jsonResponse(qt)],
      ["/v1/system/currencies", "GET", () => jsonResponse(page([{ code: "USD", minor_units: 2 }]))],
      ["/v1/users/lookup", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }]))],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/quotations/7" });

    // ① QT 상세 → PI 만들기 (은행·유효기간만 입력)
    fireEvent.click(await screen.findByRole("button", { name: "PI 만들기" }));
    let dialog = await screen.findByRole("dialog", { name: "PI 만들기" });
    fireEvent.change(within(dialog).getByLabelText("유효기간 *"), { target: { value: "2026-10-30" } });
    await within(dialog).findByRole("option", { name: "신한 USD — Shinhan Bank" });
    fireEvent.change(within(dialog).getByLabelText(/입금 은행 계좌/), { target: { value: "2" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "미리보기" }));
    await within(dialog).findByText(/아직 저장되지 않았습니다/);
    fireEvent.click(within(dialog).getByRole("button", { name: "생성 확정" }));

    // ② PI 상세로 이동 — 여기서 이어서 SO 만들기
    expect(await screen.findByRole("heading", { name: /PI-2026-0001/ })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "SO 만들기" }));
    dialog = await screen.findByRole("dialog", { name: "수주 만들기" });
    fireEvent.change(within(dialog).getByLabelText("바이어 PO번호 (선택)"), { target: { value: "PO-2026-001" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "다음: 내용 확인" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "생성 확정" }));

    // ③ SO 상세 — 접수 상태, PI에서 온 값이 그대로(서버 복사), 문서 흐름에 QT→PI→SO
    expect(await screen.findByRole("heading", { name: /SO-2026-0001/ })).toBeInTheDocument();
    expect(within(screen.getByRole("heading", { name: /SO-2026-0001/ })).getByText("접수")).toBeInTheDocument();
    expect(screen.getByText("수분 세럼")).toBeInTheDocument();
    expect(screen.getByText("12.50")).toBeInTheDocument();
    const flow = await screen.findByRole("heading", { name: "문서 흐름" });
    const panel = flow.closest("section") as HTMLElement;
    await waitFor(() => expect(within(panel).getAllByRole("listitem")).toHaveLength(3));
    expect(within(panel).getByRole("link", { name: "QT-2026-0001" })).toHaveAttribute("href", "/quotations/7");
    expect(within(panel).getByRole("link", { name: "PI-2026-0001" })).toHaveAttribute("href", "/proforma-invoices/5");
    expect(within(panel).getByText("SO-2026-0001").closest("li")).toHaveAttribute("aria-current", "true");

    // 각 단계 요청 본문 — 원천 값을 다시 넣지 않았다.
    const posts = calls.filter((c) => c.method === "POST");
    const pick = (suffix: string) => posts.find((c) => c.url.endsWith(suffix));
    const preview = pick("/v1/quotations/7/proforma-invoices/preview");
    const createPi = pick("/v1/quotations/7/proforma-invoices");
    const createSo = pick("/v1/proforma-invoices/5/sales-orders");
    expect(preview && createPi && createSo).toBeTruthy();
    for (const request of [preview, createPi, createSo]) {
      for (const key of ORIGIN_KEYS) expect(request?.body).not.toHaveProperty(key);
    }
    expect(createSo?.body).toEqual({ version: 2, buyer_po_no: "PO-2026-001" });
    // 생성 요청마다 멱등 키가 있고 서로 다르다(미리보기는 키를 소비하지 않는 비저장 호출이라 제외).
    const keys = [createPi, createSo].map((c) => c?.headers["Idempotency-Key"]);
    for (const key of keys) expect(key).toBeTruthy();
    expect(new Set(keys).size).toBe(2);
    // 사슬 전체에서 사용자가 넣은 값은 은행·유효기간·바이어 PO뿐이다.
    expect(Object.keys(createPi?.body ?? {}).sort()).toEqual(["bank_account_id", "doc_date", "valid_until", "version"]);
  });
});
