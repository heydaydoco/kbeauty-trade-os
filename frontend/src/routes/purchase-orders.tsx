// 발주(PO) 목록 (S3-1 PR-8b — design-A A12 / design-F F5·F15 / G-01 전표 화면 규약 / ADR-0057 원가 마스킹).
//
// 규약: 한국어 break-keep · 좁은 셀·헤더 nowrap · 숫자 가운데 정렬 · 금액은 서버 문자열 그대로(산술 0).
// ★ 원가 열(합계)은 서버 응답에 원가 키가 있을 때만 그린다 — 조회 전용 역할은 열 자체가 없다(빈 칸·깨짐 없음).
// 쓰기(발주 만들기)는 무역·관리자에게만 보인다 — 표시 편의일 뿐 역할은 서버가 다시 막는다(§18.1).
// CSV는 서버가 역할별로 만든 파일을 그대로 내려받는다(화면은 열 구성을 모른다).

import { useState } from "react";
import { Link } from "react-router";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { errorMessage } from "../lib/api-errors";
import { PO_KIND_LABEL, purchaseOrderStatusLabel, statusBadgeClass } from "../lib/doc-status";
import { downloadFile } from "../lib/download";
import { usePagedList } from "../lib/paging";
import {
  PURCHASE_ORDERS_QUERY_KEY,
  hasCostColumns,
  type PurchaseOrderSummary,
} from "../lib/purchase-order";
import { hasRole, useSession } from "../lib/session";

/** 사람이 만들 수 있는 상태만 필터로 둔다(입고 후반 3값은 S4-1에서 전이가 생긴다). */
export const PO_STATUS_FILTERS = ["ISSUED", "SUPPLIER_CONFIRMED", "CANCELLED"] as const;

interface Filters {
  status: string;
  poKind: string;
  q: string;
  dateFrom: string;
  dateTo: string;
}

/** 목록·CSV가 같은 필터 문자열을 쓴다(CSV는 지금 보는 조건 그대로 내려받는다). */
function filterQuery(filters: Filters) {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.poKind) params.set("po_kind", filters.poKind);
  if (filters.q.trim()) params.set("q", filters.q.trim());
  if (filters.dateFrom) params.set("date_from", filters.dateFrom);
  if (filters.dateTo) params.set("date_to", filters.dateTo);
  return params.toString();
}

export function PurchaseOrderStatusBadge({ status }: { status: string }) {
  return (
    <span className={`cell-nowrap rounded border px-2 py-0.5 text-xs ${statusBadgeClass(status)}`}>
      {purchaseOrderStatusLabel(status)}
    </span>
  );
}

export function PurchaseOrderListPage() {
  const { me } = useSession();
  const canWrite = hasRole(me, "TRADE");
  const [filters, setFilters] = useState<Filters>({ status: "", poKind: "", q: "", dateFrom: "", dateTo: "" });
  const [csvError, setCsvError] = useState<string | null>(null);

  const query = filterQuery(filters);
  // ★ 쿼리 키에 필터를 넣는다 — usePagedList의 키는 경로를 자동으로 포함하지 않는다.
  const list = usePagedList<PurchaseOrderSummary>(
    [...PURCHASE_ORDERS_QUERY_KEY, "list", query],
    query ? `/v1/purchase-orders?${query}` : "/v1/purchase-orders",
  );

  async function exportCsv() {
    setCsvError(null);
    try {
      await downloadFile(
        query ? `/v1/purchase-orders/export.csv?${query}` : "/v1/purchase-orders/export.csv",
        "발주서목록.csv",
      );
    } catch (caught) {
      setCsvError(errorMessage(caught, "내려받지 못했습니다.", "발주"));
    }
  }

  const rows = list.data?.items ?? [];
  const showCost = hasCostColumns(rows);

  return (
    <section>
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">발주 (PO)</h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            공급사·OEM에 내는 구매 발주서입니다. 초안이 없고, 만들어 확정하면 곧바로 발행·동결됩니다.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void exportCsv()}
            className="cell-nowrap rounded border border-gray-300 px-3 py-2 text-sm"
          >
            CSV 내보내기
          </button>
          {canWrite && (
            <Link
              to="/purchase-orders/new"
              className="cell-nowrap rounded bg-gray-900 px-3 py-2 text-sm text-white"
            >
              발주 만들기
            </Link>
          )}
        </div>
      </header>
      {csvError && (
        <p role="alert" className="mt-2 text-sm text-signal-red">
          {csvError}
        </p>
      )}

      <form
        role="search"
        aria-label="발주 필터"
        className="mt-4 flex flex-wrap items-end gap-3 text-sm"
        onSubmit={(event) => event.preventDefault()}
      >
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">상태</span>
          <select
            name="status"
            value={filters.status}
            onChange={(event) => setFilters({ ...filters, status: event.target.value })}
            className="rounded border border-gray-300 px-3 py-2"
          >
            <option value="">전체</option>
            {PO_STATUS_FILTERS.map((code) => (
              <option key={code} value={code}>
                {purchaseOrderStatusLabel(code)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">구분</span>
          <select
            name="po_kind"
            value={filters.poKind}
            onChange={(event) => setFilters({ ...filters, poKind: event.target.value })}
            className="rounded border border-gray-300 px-3 py-2"
          >
            <option value="">전체</option>
            {Object.entries(PO_KIND_LABEL).map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">발주번호·공급사명·SKU 코드</span>
          <input
            name="q"
            value={filters.q}
            maxLength={100}
            onChange={(event) => setFilters({ ...filters, q: event.target.value })}
            className="rounded border border-gray-300 px-3 py-2"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">증빙일 시작</span>
          <input
            type="date"
            name="date_from"
            value={filters.dateFrom}
            onChange={(event) => setFilters({ ...filters, dateFrom: event.target.value })}
            className="rounded border border-gray-300 px-3 py-2"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">증빙일 끝</span>
          <input
            type="date"
            name="date_to"
            value={filters.dateTo}
            onChange={(event) => setFilters({ ...filters, dateTo: event.target.value })}
            className="rounded border border-gray-300 px-3 py-2"
          />
        </label>
      </form>

      <ListPager data={list.data} page={list.page} onPageChange={list.setPage} className="mt-4" />

      <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint={
            query
              ? "조건에 맞는 발주가 없습니다. 필터를 바꿔 보세요."
              : canWrite
                ? "아직 발주가 없습니다. '발주 만들기'로 시작하세요."
                : "아직 발주가 없습니다."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="cell-nowrap px-4 py-2">발주번호</th>
                <th className="cell-nowrap px-4 py-2 text-center">증빙일</th>
                <th className="cell-nowrap px-4 py-2 text-center">상태</th>
                <th className="cell-nowrap px-4 py-2 text-center">구분</th>
                <th className="cell-nowrap px-4 py-2">공급사</th>
                <th className="cell-nowrap px-4 py-2 text-center">OC 일자</th>
                {showCost && <th className="cell-nowrap px-4 py-2 text-center">합계</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">
                    <Link to={`/purchase-orders/${row.id}`} className="underline">
                      {row.doc_number}
                    </Link>
                  </td>
                  <td className="num cell-nowrap px-4 py-2">{row.doc_date}</td>
                  <td className="px-4 py-2 text-center">
                    <PurchaseOrderStatusBadge status={row.status} />
                  </td>
                  <td className="cell-nowrap px-4 py-2 text-center">{PO_KIND_LABEL[row.po_kind] ?? row.po_kind}</td>
                  <td className="break-keep px-4 py-2">{row.supplier_name}</td>
                  <td className="num cell-nowrap px-4 py-2">{row.oc_received_on ?? "—"}</td>
                  {showCost && (
                    <td className="num cell-nowrap px-4 py-2">
                      {row.total_text === undefined ? "—" : `${row.total_text} ${row.currency ?? ""}`.trim()}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>
    </section>
  );
}
