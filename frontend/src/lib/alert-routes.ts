// 알림 대상(entity_type) → 화면 경로 표 (S3-1 G-05 — 알림 이동 매핑 일반화).
//
// ★ 새 전표·알림 종류는 이 표에 한 줄만 더한다(PR-6 proforma_invoices, PR-7 sales_orders, PR-8 purchase_orders).
//   표에 없는 종류는 링크 없이 글자로만 보인다 — 없는 화면으로 보내 404를 만들지 않는다.

const ALERT_ROUTES: Record<string, (id: number) => string> = {
  quotations: (id) => `/quotations/${id}`,
  proforma_invoices: (id) => `/proforma-invoices/${id}`,
  sales_orders: (id) => `/sales-orders/${id}`,
  purchase_orders: (id) => `/purchase-orders/${id}`,
  // 승인 알림(요청·결과·대결·정체 독촉)은 모두 entity_type="approvals" — 서버 approvals/alerts.py (PR-9b).
  approvals: (id) => `/approvals/${id}`,
};

export function alertRoute(entityType: string | null, entityId: number | null): string | null {
  if (entityType === null || entityId === null) return null;
  const build = ALERT_ROUTES[entityType];
  return build === undefined ? null : build(entityId);
}
