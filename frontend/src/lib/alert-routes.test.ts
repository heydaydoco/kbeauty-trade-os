import { describe, expect, it } from "vitest";
import { alertRoute, alertTargetLabel } from "./alert-routes";

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

  it("shipments 알림(선적 이벤트·기일)은 선적 상세로 이동한다 — 마일스톤 id 행은 없다", () => {
    expect(alertRoute("shipments", 3)).toBe("/shipments/3");
    expect(alertRoute("shipments", null)).toBeNull();
    expect(alertRoute("milestones", 3)).toBeNull();
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

  it("대상 종류는 한국어 화면 이름으로 보인다 — 표 이름 원문(shipments 등)을 그대로 보이지 않는다 (S3-2 PR-8 워크스루)", () => {
    expect(alertTargetLabel("shipments")).toBe("선적");
    expect(alertTargetLabel("quotations")).toBe("견적");
    expect(alertTargetLabel("proforma_invoices")).toBe("PI");
    expect(alertTargetLabel("sales_orders")).toBe("수주");
    expect(alertTargetLabel("purchase_orders")).toBe("발주");
    expect(alertTargetLabel("order_intakes")).toBe("주문 접수");
    expect(alertTargetLabel("approvals")).toBe("승인");
    expect(alertTargetLabel("certifications")).toBe("인증");
    expect(alertTargetLabel("backups")).toBe("기타");
  });

  it("이동 표의 모든 종류에 화면 이름이 있다(새 종류를 이동 표에만 더하면 실패)", () => {
    for (const type of ["quotations", "proforma_invoices", "sales_orders", "order_intakes", "purchase_orders", "shipments", "approvals"]) {
      expect(alertRoute(type, 1)).not.toBeNull();
      expect(alertTargetLabel(type)).not.toBe("기타");
    }
  });
});
