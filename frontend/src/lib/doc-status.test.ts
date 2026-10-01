import { describe, expect, it } from "vitest";
import {
  BALANCE_ANCHOR_LABEL,
  PO_BALANCE_ANCHOR_LABEL,
  canCancelPurchaseOrder,
  canConfirmPurchaseOrder,
  canEditPurchaseOrderAssignee,
  canEditPurchaseOrderNote,
  canEditPurchaseOrderOc,
  purchaseOrderStatusLabel,
  canCancelQuotation,
  canEditQuotation,
  canIssueQuotation,
  canCancelProforma,
  canCreateProformaFrom,
  canCancelSalesOrder,
  canCreateSalesOrderFromProforma,
  canCreateSalesOrderFromQuotation,
  canEditSalesOrder,
  canHoldSalesOrder,
  canResumeSalesOrder,
  docStatusLabel,
  salesOrderStatusLabel,
  canReviseQuotation,
  proformaStatusLabel,
  quotationStatusLabel,
  statusBadgeClass,
} from "./doc-status";

describe("견적 상태별 가능 동작 (서버 전이표와 같아야 한다)", () => {
  it.each([
    ["DRAFT", true, true, true, false],
    ["ISSUED", false, false, true, true],
    ["CONVERTED", false, false, false, false],
    ["EXPIRED", false, false, false, false],
    ["CANCELLED", false, false, false, false],
  ])("%s", (status, edit, issue, cancel, revise) => {
    expect(canEditQuotation(status)).toBe(edit);
    expect(canIssueQuotation(status)).toBe(issue);
    expect(canCancelQuotation(status)).toBe(cancel);
    expect(canReviseQuotation(status)).toBe(revise);
  });

  it("모르는 상태 코드는 감추지 않고 그대로 보인다", () => {
    expect(quotationStatusLabel("WEIRD")).toBe("WEIRD");
    expect(quotationStatusLabel("ISSUED")).toBe("발행");
  });
});

describe("PI 상태별 가능 동작 (서버 전이표와 같아야 한다)", () => {
  it.each([
    ["ISSUED", true],
    ["PARTIALLY_PAID", false],
    ["PAID", false],
    ["EXPIRED", false],
    ["CANCELLED", false],
  ])("%s 취소 가능=%s", (status, cancel) => {
    expect(canCancelProforma(status)).toBe(cancel);
  });

  it("라벨·배지: 입금 상태는 진하게, 취소·만료는 취소선", () => {
    expect(proformaStatusLabel("PARTIALLY_PAID")).toBe("일부입금");
    expect(proformaStatusLabel("PAID")).toBe("입금완료");
    expect(proformaStatusLabel("WEIRD")).toBe("WEIRD");
    expect(statusBadgeClass("PAID")).toContain("bg-gray-900");
    expect(statusBadgeClass("CANCELLED")).toContain("line-through");
  });

  it.each([
    ["DRAFT", false],
    ["ISSUED", true],
    ["CONVERTED", true],
    ["EXPIRED", false],
    ["CANCELLED", false],
  ])("원천 QT %s → PI 만들기 가능=%s", (status, ok) => {
    expect(canCreateProformaFrom(status)).toBe(ok);
  });
});

describe("SO 상태별 가능 동작 (서버 전이표와 같아야 한다)", () => {
  it.each([
    ["RECEIVED", true, true, false, true],
    ["CONFIRMED", false, true, false, true],
    ["ON_HOLD", false, false, true, true],
    ["CANCELLED", false, false, false, false],
    ["COMPLETED", false, false, false, false],
  ])("%s", (status, edit, hold, resume, cancel) => {
    expect(canEditSalesOrder(status)).toBe(edit);
    expect(canHoldSalesOrder(status)).toBe(hold);
    expect(canResumeSalesOrder(status)).toBe(resume);
    expect(canCancelSalesOrder(status)).toBe(cancel);
  });

  it("라벨·배지: 확정은 진하게, 보류는 회색 채움, 취소는 취소선, 모르는 코드는 그대로", () => {
    expect(salesOrderStatusLabel("RECEIVED")).toBe("접수");
    expect(salesOrderStatusLabel("ON_HOLD")).toBe("보류");
    expect(salesOrderStatusLabel("WEIRD")).toBe("WEIRD");
    expect(statusBadgeClass("CONFIRMED")).toContain("bg-gray-900");
    expect(statusBadgeClass("ON_HOLD")).toContain("bg-gray-200");
    expect(statusBadgeClass("CANCELLED")).toContain("line-through");
  });

  it.each([
    ["ISSUED", true],
    ["CONVERTED", true],
    ["DRAFT", false],
    ["EXPIRED", false],
    ["CANCELLED", false],
  ])("원천 QT %s → SO 만들기 가능=%s", (status, ok) => {
    expect(canCreateSalesOrderFromQuotation(status)).toBe(ok);
  });

  it.each([
    ["ISSUED", true],
    ["PARTIALLY_PAID", true],
    ["PAID", true],
    ["EXPIRED", false],
    ["CANCELLED", false],
  ])("원천 PI %s → SO 만들기 가능=%s", (status, ok) => {
    expect(canCreateSalesOrderFromProforma(status)).toBe(ok);
  });

  it("문서 흐름 노드의 상태 라벨은 전표 종류별로 다르다", () => {
    expect(docStatusLabel("QUOTATION", "ISSUED")).toBe("발행");
    expect(docStatusLabel("PROFORMA_INVOICE", "PAID")).toBe("입금완료");
    expect(docStatusLabel("SALES_ORDER", "RECEIVED")).toBe("접수");
    expect(docStatusLabel("PURCHASE_ORDER", "SUPPLIER_CONFIRMED")).toBe("공급사 확인");
    expect(docStatusLabel("UNKNOWN_KIND", "ISSUED")).toBe("ISSUED");
  });
});

describe("PO 상태별 가능 동작 (서버 전이표와 같아야 한다)", () => {
  it.each([
    ["ISSUED", true, true],
    ["SUPPLIER_CONFIRMED", false, true],
    ["PARTIALLY_RECEIVED", false, false],
    ["FULLY_RECEIVED", false, false],
    ["CLOSED", false, false],
    ["CANCELLED", false, false],
  ])("%s → 공급사 확인 가능=%s · 취소 가능=%s", (status, confirm, cancel) => {
    expect(canConfirmPurchaseOrder(status)).toBe(confirm);
    expect(canCancelPurchaseOrder(status)).toBe(cancel);
  });

  it("취소된 발주도 내부 메모는 열려 있고(원가 오기 정정) 담당자는 닫힌다, OC 열은 공급사 확인 상태에서만 열린다", () => {
    expect(canEditPurchaseOrderNote("CANCELLED")).toBe(true);
    expect(canEditPurchaseOrderAssignee("ISSUED")).toBe(true);
    expect(canEditPurchaseOrderAssignee("CANCELLED")).toBe(false);
    expect(canEditPurchaseOrderOc("SUPPLIER_CONFIRMED")).toBe(true);
    expect(canEditPurchaseOrderOc("ISSUED")).toBe(false);
  });

  it("PO 라벨·배지: 6값 라벨, 모르는 값은 그대로", () => {
    expect(purchaseOrderStatusLabel("ISSUED")).toBe("발행");
    expect(purchaseOrderStatusLabel("PARTIALLY_RECEIVED")).toBe("부분입고");
    expect(purchaseOrderStatusLabel("X")).toBe("X");
    expect(statusBadgeClass("SUPPLIER_CONFIRMED")).toBe(statusBadgeClass("ISSUED"));
  });

  it("입고 확정일 기산점은 PO 전용 표에만 있다(판매 전표 선택지에 섞이지 않는다)", () => {
    expect(PO_BALANCE_ANCHOR_LABEL.RECEIPT_DATE).toBe("입고 확정일");
    expect(BALANCE_ANCHOR_LABEL).not.toHaveProperty("RECEIPT_DATE");
  });
});
