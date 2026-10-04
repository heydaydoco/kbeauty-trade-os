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
    expect(flowRoute("SHIPMENT", 5)).toBe("/shipments/5");
    expect(flowRoute("PURCHASE_ORDER", 4)).toBeNull();
  });

  // S3-2 PR-3b(design-D D8) — 서버(PR-3c)는 SO 노드 바로 뒤에 그 SO의 수출선적 노드를 준다(부모 = SO, 취소 선적 포함).
  it("SHIPMENT 노드: SO 아래 한 단계 들여쓰기·'선적' 종류·선적 상태 라벨·선적 상세 링크, 선적에서 들어오면 그 선적이 현재 문서", async () => {
    const flow = chainFlow("PROFORMA_INVOICE");
    flow.nodes.push(
      flowNode({
        kind: "SHIPMENT",
        id: 31,
        doc_number: "SH-2026-0001",
        status: "RELEASE_ORDERED",
        total_amount: 5000,
        total_text: "50.00",
        parent_kind: "SALES_ORDER",
        parent_id: 9,
        is_current: true,
      }),
      flowNode({
        kind: "SHIPMENT",
        id: 32,
        doc_number: "SH-2026-0002",
        status: "CANCELLED",
        total_amount: 2500,
        total_text: "25.00",
        parent_kind: "SALES_ORDER",
        parent_id: 9,
      }),
    );
    flow.nodes[1] = { ...flow.nodes[1]!, is_current: false };
    const { calls } = stubFetch(TRADER, [["/v1/document-flow/SHIPMENT/31", "GET", () => jsonResponse(flow)]]);
    renderWithProviders(<DocumentFlowPanel kind="SHIPMENT" id={31} />);

    const current = (await screen.findByText("SH-2026-0001")).closest("li") as HTMLElement;
    expect(current).toHaveAttribute("aria-current", "true");
    expect(within(current).getByText("선적")).toBeInTheDocument();
    expect(within(current).getByText("출고지시")).toBeInTheDocument();
    expect(within(current).getByText("50.00 USD")).toBeInTheDocument();
    expect(current.style.marginLeft).toBe("4.5rem"); // QT 0 → PI 1.5 → SO 3 → 선적 4.5
    // 취소된 선적도 이력으로 보이고, 현재 문서가 아니면 선적 상세로 이동한다.
    expect(screen.getByRole("link", { name: "SH-2026-0002" })).toHaveAttribute("href", "/shipments/32");
    expect(within(itemOf("SH-2026-0002")).getByText("취소")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "SO-2026-0001" })).toHaveAttribute("href", "/sales-orders/9");
    expect(calls.some((c) => c.url.endsWith("/v1/document-flow/SHIPMENT/31"))).toBe(true);
  });
});
