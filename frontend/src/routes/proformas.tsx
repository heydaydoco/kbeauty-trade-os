// PI(선수금 청구서) 목록 (S3-1 PR-6b — design-A PI 화면 / G-01 전표 화면 규약).
//
// 규약: 한국어 break-keep · 좁은 셀·헤더 nowrap · 숫자 가운데 정렬 · 금액은 서버 문자열 그대로(산술 0).
// PI는 목록에서 만들지 않는다 — 견적 상세의 'PI 만들기'(참조 생성)로만 태어난다(재입력 화면 없음).

import { useState } from "react";
import { Link } from "react-router";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { errorMessage } from "../lib/api-errors";
import { proformaStatusLabel, statusBadgeClass } from "../lib/doc-status";
import { downloadFile } from "../lib/download";
import { usePagedList } from "../lib/paging";
import type { ProformaSummary } from "../lib/proforma";
import { PROFORMAS_QUERY_KEY } from "../lib/proforma";

export const PI_STATUS_FILTERS = ["ISSUED", "PARTIALLY_PAID", "PAID", "EXPIRED", "CANCELLED"] as const;

/** 목록·CSV가 같은 필터 문자열을 쓴다(CSV는 지금 보는 조건 그대로 내려받는다). */
function filterQuery(filters: { status: string; q: string; dateFrom: string; dateTo: string }) {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.q.trim()) params.set("q", filters.q.trim());
  if (filters.dateFrom) params.set("date_from", filters.dateFrom);
  if (filters.dateTo) params.set("date_to", filters.dateTo);
  return params.toString();
}

/** 상태 배지 + 만료 배지(미입금 발행인데 유효기간이 지난 PI — 서버 파생 `is_lapsed`). 색만으로 구분하지 않는다. */
export function ProformaStatusBadge({ status, lapsed = false }: { status: string; lapsed?: boolean }) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      <span className={`cell-nowrap rounded border px-2 py-0.5 text-xs ${statusBadgeClass(status)}`}>
        {proformaStatusLabel(status)}
      </span>
      {lapsed && status === "ISSUED" && (
        <span className="cell-nowrap rounded border border-signal-red px-2 py-0.5 text-xs text-signal-red">
          유효기간 경과
        </span>
      )}
    </span>
  );
}

export function ProformaListPage() {
  const [filters, setFilters] = useState({ status: "", q: "", dateFrom: "", dateTo: "" });
  const [csvError, setCsvError] = useState<string | null>(null);

  const query = filterQuery(filters);
  // ★ 쿼리 키에 필터를 넣는다 — usePagedList의 키는 경로를 자동으로 포함하지 않는다.
  const list = usePagedList<ProformaSummary>(
    [...PROFORMAS_QUERY_KEY, "list", query],
    query ? `/v1/proforma-invoices?${query}` : "/v1/proforma-invoices",
  );

  async function exportCsv() {
    setCsvError(null);
    try {
      await downloadFile(
        query ? `/v1/proforma-invoices/export.csv?${query}` : "/v1/proforma-invoices/export.csv",
        "PI목록.csv",
      );
    } catch (caught) {
      setCsvError(errorMessage(caught, "내려받지 못했습니다."));
    }
  }

  return (
    <section>
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">PI (선수금 청구서)</h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            발행된 견적에서 만듭니다. 만들면 바로 발행·동결되고, 잘못되면 취소하고 새로 만듭니다.
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
        aria-label="PI 필터"
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
            {PI_STATUS_FILTERS.map((code) => (
              <option key={code} value={code}>
                {proformaStatusLabel(code)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">PI번호·바이어명</span>
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
              "조건에 맞는 PI가 없습니다. 필터를 바꿔 보세요."
            ) : (
              <>
                아직 PI가 없습니다. <Link to="/quotations" className="underline">견적</Link>을 발행한 뒤 견적 상세의
                'PI 만들기'로 만드세요.
              </>
            )
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="cell-nowrap px-4 py-2">PI번호</th>
                <th className="cell-nowrap px-4 py-2 text-center">증빙일</th>
                <th className="cell-nowrap px-4 py-2 text-center">상태</th>
                <th className="cell-nowrap px-4 py-2">바이어</th>
                <th className="cell-nowrap px-4 py-2">원천 견적</th>
                <th className="cell-nowrap px-4 py-2 text-center">합계</th>
                <th className="cell-nowrap px-4 py-2 text-center">유효기간</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((row) => (
                <tr key={row.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">
                    <Link to={`/proforma-invoices/${row.id}`} className="underline">
                      {row.doc_number}
                    </Link>
                  </td>
                  <td className="num cell-nowrap px-4 py-2">{row.doc_date}</td>
                  <td className="px-4 py-2 text-center">
                    <ProformaStatusBadge status={row.status} lapsed={row.is_lapsed} />
                  </td>
                  <td className="break-keep px-4 py-2">{row.buyer_name}</td>
                  <td className="cell-nowrap px-4 py-2">
                    <Link to={`/quotations/${row.qt_id}`} className="underline">
                      {row.qt_doc_number ?? `#${row.qt_id}`}
                    </Link>
                  </td>
                  <td className="num cell-nowrap px-4 py-2">
                    {row.total_text} {row.currency}
                  </td>
                  <td className="num cell-nowrap px-4 py-2">{row.valid_until}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>
    </section>
  );
}
