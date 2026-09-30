// 문서 흐름 패널 (S3-1 PR-7b — design-integrated G-03 / DESIGN §14 ⑦): QT → PI → SO 계보를 상태 배지·링크로 보인다.
//
// 서버 `GET /v1/document-flow/{kind}/{id}`가 사슬 전체(뿌리 QT 아래 위→아래 순서, 취소·만료 전표 포함)를 준다 — 화면은 그대로 그린다.
// 금액은 서버 문자열(total_text)만, 프런트 산술 0. 읽기 전용이라 권한별 분기가 없다(원가·마진 필드 없음).
// 전표 상세(QT·PI·SO)가 같은 컴포넌트를 쓴다 — 재입력 없이 관통되는 흐름이 한눈에 보이게.

import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { apiFetch } from "../lib/api";
import { errorMessage } from "../lib/api-errors";
import { docStatusLabel, statusBadgeClass } from "../lib/doc-status";
import { documentFlowKey, type DocumentFlow, type FlowNode } from "../lib/sales-order";

export type FlowKind = "QUOTATION" | "PROFORMA_INVOICE" | "SALES_ORDER";

const KIND_LABEL: Record<string, string> = {
  QUOTATION: "견적",
  PROFORMA_INVOICE: "PI",
  SALES_ORDER: "수주",
};

export function flowRoute(kind: string, id: number): string | null {
  switch (kind) {
    case "QUOTATION":
      return `/quotations/${id}`;
    case "PROFORMA_INVOICE":
      return `/proforma-invoices/${id}`;
    case "SALES_ORDER":
      return `/sales-orders/${id}`;
    default:
      return null;
  }
}

/** 부모 링크를 따라 올라가 들여쓰기 단계를 센다(뿌리 0). 순환 방어로 상한을 둔다. */
function depthOf(node: FlowNode, byKey: Map<string, FlowNode>): number {
  let depth = 0;
  let cursor: FlowNode | undefined = node;
  while (cursor && cursor.parent_kind !== null && cursor.parent_id !== null && depth < 5) {
    cursor = byKey.get(`${cursor.parent_kind}:${cursor.parent_id}`);
    if (cursor) depth += 1;
  }
  return depth;
}

export function DocumentFlowPanel({ kind, id }: { kind: FlowKind; id: number }) {
  const flow = useQuery({
    queryKey: documentFlowKey(kind, id),
    queryFn: () => apiFetch<DocumentFlow>(`/v1/document-flow/${kind}/${id}`),
    enabled: Number.isInteger(id) && id > 0,
    // 다른 화면에서 전표를 만들거나 취소하고 돌아왔을 때 옛 계보가 남지 않게.
    staleTime: 0,
  });

  const nodes = flow.data?.nodes ?? [];
  const byKey = new Map(nodes.map((node) => [`${node.kind}:${node.id}`, node]));

  return (
    <section aria-labelledby="document-flow-title" className="rounded-lg border border-gray-200 p-4">
      <h2 id="document-flow-title" className="text-lg font-semibold">
        문서 흐름
      </h2>
      <p className="mt-1 break-keep text-xs text-gray-500">
        견적 → PI → 수주로 이어진 전표입니다. 취소·만료된 전표도 이력으로 함께 보입니다.
      </p>
      {flow.isPending && <p className="mt-3 text-sm text-gray-500">불러오는 중…</p>}
      {flow.error && (
        <p role="alert" className="mt-3 break-keep text-sm text-signal-red">
          {errorMessage(flow.error, "문서 흐름을 불러오지 못했습니다.", "전표")}
        </p>
      )}
      {flow.data && nodes.length === 0 && <p className="mt-3 text-sm text-gray-500">이어진 문서가 없습니다.</p>}
      {nodes.length > 0 && (
        <ol className="mt-3 grid gap-2">
          {nodes.map((node) => {
            const href = flowRoute(node.kind, node.id);
            return (
              <li
                key={`${node.kind}:${node.id}`}
                style={{ marginLeft: `${depthOf(node, byKey) * 1.5}rem` }}
                aria-current={node.is_current ? "true" : undefined}
                className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded border px-3 py-2 text-sm ${
                  node.is_current ? "border-gray-900 bg-gray-50" : "border-gray-200"
                }`}
              >
                <span className="cell-nowrap text-xs text-gray-500">{KIND_LABEL[node.kind] ?? node.kind}</span>
                {node.is_current || href === null ? (
                  <span className="cell-nowrap font-medium">{node.doc_number}</span>
                ) : (
                  <Link to={href} className="cell-nowrap underline">
                    {node.doc_number}
                  </Link>
                )}
                <span
                  className={`cell-nowrap rounded border px-2 py-0.5 text-xs ${statusBadgeClass(node.status)}`}
                >
                  {docStatusLabel(node.kind, node.status)}
                </span>
                <span className="num cell-nowrap text-gray-600">{node.doc_date}</span>
                {node.total_text !== null && (
                  <span className="num cell-nowrap text-gray-600">
                    {node.total_text} {node.currency}
                  </span>
                )}
                {node.is_current && <span className="cell-nowrap text-xs font-medium">현재 문서</span>}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
