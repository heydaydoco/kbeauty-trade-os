// 수주(SO) 목록 (S3-1 PR-7b — design-A SO 화면 / G-01 전표 화면 규약).
//
// 규약: 한국어 break-keep · 좁은 셀·헤더 nowrap · 숫자 가운데 정렬 · 금액은 서버 문자열 그대로(산술 0).
// SO는 목록에서 만들지 않는다 — 견적·PI 상세의 'SO 만들기'(참조 생성)로만 태어난다(재입력 화면 없음).

import { useState } from "react";
import { Link } from "react-router";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { errorMessage } from "../lib/api-errors";
import { salesOrderStatusLabel, statusBadgeClass } from "../lib/doc-status";
import { downloadFile } from "../lib/download";
import { usePagedList } from "../lib/paging";
import { SALES_ORDERS_QUERY_KEY, type SalesOrderSummary } from "../lib/sales-order";

/**
 * 도달 가능한 상태만 필터로 둔다(예약 상태 3값 — 부분할당·할당완료·완료 — 은 아직 전이가 없다).
 * 선적중(IN_SHIPMENT)은 S3-2 PR-3a부터 첫 선적 생성 시 자동 수렴으로 도달한다(R-21 — 없으면 선적 후 수주가 필터로 안 잡힌다).
 */
export const SO_STATUS_FILTERS = ["RECEIVED", "CONFIRMED", "IN_SHIPMENT", "ON_HOLD", "CANCELLED"] as const;

/** 목록·CSV가 같은 필터 문자열을 쓴다(CSV는 지금 보는 조건 그대로 내려받는다). */
function filterQuery(filters: { status: string; q: string; dateFrom: string; dateTo: string }) {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.q.trim()) params.set("q", filters.q.trim());
  if (filters.dateFrom) params.set("date_from", filters.dateFrom);
  if (filters.dateTo) params.set("date_to", filters.dateTo);
  return params.toString();
}

export function SalesOrderStatusBadge({ status }: { status: string }) {
  return (
    <span className={`cell-nowrap rounded border px-2 py-0.5 text-xs ${statusBadgeClass(status)}`}>
      {salesOrderStatusLabel(status)}
    </span>
  );
}

export function SalesOrderListPage() {
  const [filters, setFilters] = useState({ status: "", q: "", dateFrom: "", dateTo: "" });
  const [csvError, setCsvError] = useState<string | null>(null);

  const query = filterQuery(filters);
  // ★ 쿼리 키에 필터를 넣는다 — usePagedList의 키는 경로를 자동으로 포함하지 않는다.
  const list = usePagedList<SalesOrderSummary>(
    [...SALES_ORDERS_QUERY_KEY, "list", query],
    query ? `/v1/sales-orders?${query}` : "/v1/sales-orders",
  );

  async function exportCsv() {
    setCsvError(null);
    try {
      await downloadFile(
        query ? `/v1/sales-orders/export.csv?${query}` : "/v1/sales-orders/export.csv",
        "수주목록.csv",
      );
    } catch (caught) {
      setCsvError(errorMessage(caught, "내려받지 못했습니다."));
    }
  }

  return (
    <section>
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">수주 (SO)</h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            발행된 견적이나 PI에서 만듭니다. 만들면 접수 상태로 시작하고, 접수 상태에서만 조건·라인을 고칠 수 있습니다.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void exportCsv()}
          className="cell-nowrap rounded border border-gray-300 px-3 py-2 text-sm"
        >
          CSV 내보내기
        </button>
      </header>
      {csvError && (
        <p role="alert" className="mt-2 text-sm text-signal-red">
          {csvError}
        </p>
      )}

      <form
        role="search"
        aria-label="수주 필터"
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
            {SO_STATUS_FILTERS.map((code) => (
              <option key={code} value={code}>
                {salesOrderStatusLabel(code)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">수주번호·바이어명·바이어 PO</span>
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
            query ? (
              "조건에 맞는 수주가 없습니다. 필터를 바꿔 보세요."
            ) : (
              <>
                아직 수주가 없습니다. <Link to="/quotations" className="underline">견적</Link>이나{" "}
                <Link to="/proforma-invoices" className="underline">PI</Link> 상세의 'SO 만들기'로 만드세요.
              </>
            )
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="cell-nowrap px-4 py-2">수주번호</th>
                <th className="cell-nowrap px-4 py-2 text-center">증빙일</th>
                <th className="cell-nowrap px-4 py-2 text-center">상태</th>
                <th className="cell-nowrap px-4 py-2">바이어</th>
                <th className="cell-nowrap px-4 py-2 text-center">바이어 PO</th>
                <th className="cell-nowrap px-4 py-2">원천</th>
                <th className="cell-nowrap px-4 py-2 text-center">합계</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((row) => (
                <tr key={row.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">
                    <Link to={`/sales-orders/${row.id}`} className="underline">
                      {row.doc_number}
                    </Link>
                  </td>
                  <td className="num cell-nowrap px-4 py-2">{row.doc_date}</td>
                  <td className="px-4 py-2 text-center">
                    <SalesOrderStatusBadge status={row.status} />
                  </td>
                  <td className="break-keep px-4 py-2">{row.buyer_name}</td>
                  <td className="cell-nowrap px-4 py-2 text-center">{row.buyer_po_no ?? "—"}</td>
                  <td className="cell-nowrap px-4 py-2">
                    <SourceLinks so={row} />
                  </td>
                  <td className="num cell-nowrap px-4 py-2">
                    {row.total_text} {row.currency}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>
    </section>
  );
}

/** 원천 전표 링크 — PI 경유면 PI·QT 모두, QT 직접이면 QT만, 둘 다 없으면 직접(인테이크) 수주. */
export function SourceLinks({ so }: { so: Pick<SalesOrderSummary, "qt_id" | "qt_doc_number" | "pi_id" | "pi_doc_number"> }) {
  if (so.qt_id === null && so.pi_id === null) return <span className="text-gray-500">직접 수주</span>;
  return (
    <span className="inline-flex flex-wrap gap-x-2">
      {so.qt_id !== null && (
        <Link to={`/quotations/${so.qt_id}`} className="underline">
          {so.qt_doc_number ?? `QT #${so.qt_id}`}
        </Link>
      )}
      {so.pi_id !== null && (
        <Link to={`/proforma-invoices/${so.pi_id}`} className="underline">
          {so.pi_doc_number ?? `PI #${so.pi_id}`}
        </Link>
      )}
    </span>
  );
}
