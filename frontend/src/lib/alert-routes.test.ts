import { describe, expect, it } from "vitest";
import { alertRoute } from "./alert-routes";

describe("알림 이동 표", () => {
  it("quotations 알림은 견적 상세로 이동한다", () => {
    expect(alertRoute("quotations", 12)).toBe("/quotations/12");
  });

  it("proforma_invoices 알림은 PI 상세로 이동한다", () => {
    expect(alertRoute("proforma_invoices", 5)).toBe("/proforma-invoices/5");
    expect(alertRoute("proforma_invoices", null)).toBeNull();
  });

  it("sales_orders 알림은 수주 상세로 이동한다", () => {
    expect(alertRoute("sales_orders", 9)).toBe("/sales-orders/9");
    expect(alertRoute("sales_orders", null)).toBeNull();
  });

  it("purchase_orders 알림은 발주 상세로 이동한다", () => {
    expect(alertRoute("purchase_orders", 4)).toBe("/purchase-orders/4");
    expect(alertRoute("purchase_orders", null)).toBeNull();
  });

  it("order_intakes 알림(생성·상태 변경)은 인테이크 상세로 이동한다", () => {
    expect(alertRoute("order_intakes", 21)).toBe("/orders/intakes/21");
    expect(alertRoute("order_intakes", null)).toBeNull();
  });

  it("approvals 알림(요청·결과·대결·정체 독촉)은 승인 상세로 이동한다", () => {
    expect(alertRoute("approvals", 7)).toBe("/approvals/7");
    expect(alertRoute("approvals", null)).toBeNull();
  });

  it("표에 없는 종류·빈 값은 이동하지 않는다(없는 화면으로 보내지 않는다)", () => {
    expect(alertRoute("certifications", 3)).toBeNull();
    expect(alertRoute(null, null)).toBeNull();
    expect(alertRoute("quotations", null)).toBeNull();
  });
});
