// 문서 흐름 패널 — 계보 렌더·링크·현재 문서 표시·취소 전표·빈/오류 상태 (S3-1 PR-7b).

import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubFetch } from "../test/qt-fixtures";
import { TRADER, jsonResponse, renderWithProviders } from "../test/render";
import { chainFlow, flowNode } from "../test/so-fixtures";
import { DocumentFlowPanel, flowRoute } from "./document-flow-panel";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const itemOf = (name: string) => screen.getByText(name).closest("li") as HTMLElement;

describe("문서 흐름 패널", () => {
  it("QT→PI→SO 계보를 상태 배지·서버 합계와 함께 보이고, 다른 전표는 링크, 현재 전표는 링크 없이 표시한다", async () => {
    const { calls } = stubFetch(TRADER, [["/v1/document-flow/PROFORMA_INVOICE/5", "GET", () => jsonResponse(chainFlow("PROFORMA_INVOICE"))]]);
    renderWithProviders(<DocumentFlowPanel kind="PROFORMA_INVOICE" id={5} />);

    expect(await screen.findByRole("link", { name: "QT-2026-0001" })).toHaveAttribute("href", "/quotations/7");
    expect(screen.getByRole("link", { name: "SO-2026-0001" })).toHaveAttribute("href", "/sales-orders/9");
    // 현재 문서(PI)는 링크가 아니라 글자 + '현재 문서' 표시.
    expect(screen.queryByRole("link", { name: "PI-2026-0001" })).not.toBeInTheDocument();
    const current = itemOf("PI-2026-0001");
    expect(current).toHaveAttribute("aria-current", "true");
    expect(within(current).getByText("현재 문서")).toBeInTheDocument();
    // 종류별 상태 라벨·서버 금액 문자열.
    expect(within(itemOf("QT-2026-0001")).getByText("발행")).toBeInTheDocument();
    expect(within(itemOf("SO-2026-0001")).getByText("접수")).toBeInTheDocument();
    expect(within(itemOf("QT-2026-0001")).getByText("125.00 USD")).toBeInTheDocument();
    expect(within(itemOf("SO-2026-0001")).getByText("75.00 USD")).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/v1/document-flow/PROFORMA_INVOICE/5"))).toBe(true);
  });

  it("들여쓰기: QT 뿌리 < PI < PI의 SO, QT 직접 SO는 PI와 같은 단계", async () => {
    const flow = chainFlow("QUOTATION");
    flow.nodes.push(
      flowNode({
        kind: "SALES_ORDER",
        id: 10,
        doc_number: "SO-2026-0002",
        status: "ON_HOLD",
        parent_kind: "QUOTATION",
        parent_id: 7,
      }),
    );
    stubFetch(TRADER, [["/v1/document-flow/QUOTATION/7", "GET", () => jsonResponse(flow)]]);
    renderWithProviders(<DocumentFlowPanel kind="QUOTATION" id={7} />);
    await screen.findByText("SO-2026-0002");
    const margin = (name: string) => itemOf(name).style.marginLeft;
    expect(margin("QT-2026-0001")).toBe("0rem");
    expect(margin("PI-2026-0001")).toBe("1.5rem");
    expect(margin("SO-2026-0001")).toBe("3rem");
    expect(margin("SO-2026-0002")).toBe("1.5rem");
    expect(within(itemOf("SO-2026-0002")).getByText("보류")).toBeInTheDocument();
  });

  it("취소·만료 전표도 이력으로 보인다", async () => {
    const flow = chainFlow("SALES_ORDER");
    flow.nodes[1] = { ...flow.nodes[1]!, status: "CANCELLED" };
    stubFetch(TRADER, [["/v1/document-flow/SALES_ORDER/9", "GET", () => jsonResponse(flow)]]);
    renderWithProviders(<DocumentFlowPanel kind="SALES_ORDER" id={9} />);
    expect(await within(await screen.findByText("PI-2026-0001").then((el) => el.closest("li") as HTMLElement)).findByText("취소")).toBeInTheDocument();
  });

  it("직접(인테이크) 수주는 노드 1개", async () => {
    stubFetch(TRADER, [
      [
        "/v1/document-flow/SALES_ORDER/9",
        "GET",
        () =>
          jsonResponse({
            root_kind: "SALES_ORDER",
            root_id: 9,
            nodes: [flowNode({ kind: "SALES_ORDER", id: 9, doc_number: "SO-2026-0009", status: "RECEIVED", is_current: true })],
          }),
      ],
    ]);
    renderWithProviders(<DocumentFlowPanel kind="SALES_ORDER" id={9} />);
    expect(await screen.findByText("SO-2026-0009")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  it("조회 실패는 서버 문구를 오류로 보인다", async () => {
    stubFetch(TRADER, [["/v1/document-flow/", "GET", () => jsonResponse({ error: { code: "X", message: "흐름 오류입니다." } }, 500)]]);
    renderWithProviders(<DocumentFlowPanel kind="QUOTATION" id={7} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("흐름 오류입니다.");
  });

  it("전표 종류별 이동 경로", () => {
    expect(flowRoute("QUOTATION", 1)).toBe("/quotations/1");
    expect(flowRoute("PROFORMA_INVOICE", 2)).toBe("/proforma-invoices/2");
    expect(flowRoute("SALES_ORDER", 3)).toBe("/sales-orders/3");
    expect(flowRoute("PURCHASE_ORDER", 4)).toBeNull();
  });
});
