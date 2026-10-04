// 선적 목록 `/shipments` (S3-2 PR-3b — design-D D5·D13 / PROGRESS 'S3-2 PR-3a' 인계 계약 S1 / PR-3c S20 CSV).
//
// 규약: 한국어 break-keep · 좁은 셀·헤더·국가 nowrap · 숫자·기준값 가운데 정렬 · 금액은 서버 문자열 그대로(산술 0).
// ★ 선적은 목록에서 만들지 않는다 — 수주 상세의 '선적 만들기'(SO 참조 2단)로만 태어난다(원천 없는 생성 화면 금지 — design-D D5).
// ★ 조회는 전 역할. 주소의 `?q=`·`?status=`는 첫 조건으로만 읽는다(수주 취소 409 '먼저 취소할 선적' 링크의 진입점).
// ★ 수입선적(PR-5a)은 금액 축이 없다 — 합계 칸은 수출만, 수입은 '—'(0으로 그리지 않는다).

import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { errorMessage } from "../lib/api-errors";
import { shipmentStatusLabel, statusBadgeClass } from "../lib/doc-status";
import { downloadFile } from "../lib/download";
import { usePagedList } from "../lib/paging";
import {
  SHIPMENTS_QUERY_KEY,
  SHIPMENT_STATUS_FILTERS,
  countryName,
  shipmentKindLabel,
  type ShipmentListItem,
} from "../lib/shipment";

const Q_MAX = 100;

/** 목록·CSV가 같은 필터 문자열을 쓴다(CSV는 지금 보는 조건 그대로 — 서버 PR-3c가 같은 조건 함수). */
function filterQuery(filters: { status: string; q: string }) {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.q.trim()) params.set("q", filters.q.trim());
  return params.toString();
}

export function ShipmentStatusBadge({ status }: { status: string }) {
  return (
    <span className={`cell-nowrap rounded border px-2 py-0.5 text-xs ${statusBadgeClass(status)}`}>
      {shipmentStatusLabel(status)}
    </span>
  );
}

/** "KR → US" — 이름은 보조 정보(title·스크린리더)로, 칸은 좁게(nowrap). */
export function CountryRoute({ origin, dest }: { origin: string; dest: string }) {
  const label = `${countryName(origin) ?? origin} → ${countryName(dest) ?? dest}`;
  return (
    <span className="cell-nowrap" title={label} aria-label={`출발국 ${origin} 도착국 ${dest}`}>
      {origin} → {dest}
    </span>
  );
}

/** 주소의 조건이 바뀌면(다른 화면의 링크로 다시 들어옴) 화면 상태를 새로 시작한다 — 옛 필터가 남지 않게. */
export function ShipmentListPage() {
  const [search] = useSearchParams();
  const initialStatus = search.get("status") ?? "";
  return (
    <ShipmentListView
      key={search.toString()}
      initial={{
        status: (SHIPMENT_STATUS_FILTERS as readonly string[]).includes(initialStatus) ? initialStatus : "",
        q: (search.get("q") ?? "").slice(0, Q_MAX),
      }}
    />
  );
}

function ShipmentListView({ initial }: { initial: { status: string; q: string } }) {
  const [filters, setFilters] = useState(initial);
  const [csvError, setCsvError] = useState<string | null>(null);

  const query = filterQuery(filters);
  // ★ 쿼리 키에 필터를 넣는다 — usePagedList의 키는 경로를 자동으로 포함하지 않는다.
  const list = usePagedList<ShipmentListItem>(
    [...SHIPMENTS_QUERY_KEY, "list", query],
    query ? `/v1/shipments?${query}` : "/v1/shipments",
  );

  async function exportCsv() {
    setCsvError(null);
    try {
      await downloadFile(query ? `/v1/shipments/export.csv?${query}` : "/v1/shipments/export.csv", "선적목록.csv");
    } catch (caught) {
      setCsvError(errorMessage(caught, "내려받지 못했습니다.", "선적"));
    }
  }

  return (
    <section>
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">선적</h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            확정된 수주 상세의 &lsquo;선적 만들기&rsquo;로 만듭니다(통화·환율·조건·품목은 수주에서 복사). 한 수주를 여러 번 나눠 선적할 수 있습니다.
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
        <p role="alert" className="mt-2 break-keep text-sm text-signal-red">
          {csvError}
        </p>
      )}

      <form
        role="search"
        aria-label="선적 필터"
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
            {SHIPMENT_STATUS_FILTERS.map((code) => (
              <option key={code} value={code}>
                {shipmentStatusLabel(code)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">선적번호·거래 상대</span>
          <input
            name="q"
            value={filters.q}
            maxLength={Q_MAX}
            onChange={(event) => setFilters({ ...filters, q: event.target.value })}
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
              "조건에 맞는 선적이 없습니다. 필터를 바꿔 보세요."
            ) : (
              <>
                아직 선적이 없습니다. 확정된 <Link to="/sales-orders" className="underline">수주</Link> 상세의
                &lsquo;선적 만들기&rsquo;로 만드세요.
              </>
            )
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th scope="col" className="cell-nowrap px-4 py-2">선적번호</th>
                <th scope="col" className="cell-nowrap px-4 py-2 text-center">증빙일</th>
                <th scope="col" className="cell-nowrap px-4 py-2 text-center">상태</th>
                <th scope="col" className="cell-nowrap px-4 py-2 text-center">구분</th>
                <th scope="col" className="cell-nowrap px-4 py-2">원천 전표</th>
                <th scope="col" className="cell-nowrap px-4 py-2">거래 상대</th>
                <th scope="col" className="cell-nowrap px-4 py-2 text-center">출발 → 도착</th>
                <th scope="col" className="cell-nowrap px-4 py-2 text-center">라인</th>
                <th scope="col" className="cell-nowrap px-4 py-2 text-center">합계</th>
                <th scope="col" className="cell-nowrap px-4 py-2">담당</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((row) => (
                <tr key={row.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">
                    <Link to={`/shipments/${row.id}`} className="underline">
                      {row.doc_number}
                    </Link>
                  </td>
                  <td className="num cell-nowrap px-4 py-2">{row.doc_date}</td>
                  <td className="px-4 py-2 text-center">
                    <ShipmentStatusBadge status={row.status} />
                  </td>
                  <td className="cell-nowrap px-4 py-2 text-center">{shipmentKindLabel(row.shipment_kind)}</td>
                  <td className="cell-nowrap px-4 py-2">
                    {row.source.kind === "SALES_ORDER" ? (
                      <Link to={`/sales-orders/${row.source.id}`} className="underline">
                        {row.source.doc_number}
                      </Link>
                    ) : (
                      <Link to={`/purchase-orders/${row.source.id}`} className="underline">
                        {row.source.doc_number}
                      </Link>
                    )}
                  </td>
                  <td className="break-keep px-4 py-2">{row.counterparty_name}</td>
                  <td className="px-4 py-2 text-center">
                    <CountryRoute origin={row.origin_country_code} dest={row.dest_country_code} />
                  </td>
                  <td className="num cell-nowrap px-4 py-2">{row.line_count}</td>
                  <td className="num cell-nowrap px-4 py-2">
                    {row.shipment_kind === "EXPORT" ? `${row.total_text} ${row.currency}` : "—"}
                  </td>
                  <td className="cell-nowrap px-4 py-2">{row.assignee.display_name ?? `#${row.assignee.id}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>
    </section>
  );
}
