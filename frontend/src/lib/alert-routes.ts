// 알림 대상(entity_type) → 화면 경로 표 (S3-1 G-05 — 알림 이동 매핑 일반화).
//
// ★ 새 전표·알림 종류는 이 표에 한 줄만 더한다(PR-6 proforma_invoices, PR-7 sales_orders, PR-8 purchase_orders, S3-2 PR-3b shipments).
//   표에 없는 종류는 링크 없이 글자로만 보인다 — 없는 화면으로 보내 404를 만들지 않는다.

const ALERT_ROUTES: Record<string, (id: number) => string> = {
  quotations: (id) => `/quotations/${id}`,
  proforma_invoices: (id) => `/proforma-invoices/${id}`,
  sales_orders: (id) => `/sales-orders/${id}`,
  // 오더 인테이크 알림(생성·상태 변경)은 entity_type="order_intakes" — 서버 order_intake 이벤트 (PR-13a).
  order_intakes: (id) => `/orders/intakes/${id}`,
  purchase_orders: (id) => `/purchase-orders/${id}`,
  // 선적 이벤트·기일 알림은 소유 전표 entity_type="shipments"(design-D §0 가정 6·D11 — 마일스톤 id 행은 만들지 않는다, S3-2 PR-3b).
  shipments: (id) => `/shipments/${id}`,
  // 승인 알림(요청·결과·대결·정체 독촉)은 모두 entity_type="approvals" — 서버 approvals/alerts.py (PR-9b).
  approvals: (id) => `/approvals/${id}`,
};

export function alertRoute(entityType: string | null, entityId: number | null): string | null {
  if (entityType === null || entityId === null) return null;
  const build = ALERT_ROUTES[entityType];
  return build === undefined ? null : build(entityId);
}
