// 오더 인테이크(주문 접수) 목록 (S3-1 PR-13b — design-D D1·D2·D7 / ADR-0071).
//
// 규약: 한국어 break-keep · 좁은 셀·헤더 nowrap · 숫자 가운데 정렬 · 금액은 서버 문자열 그대로(산술 0).
// ★ 막다른 PENDING 표지(`po_occupied`)·상태는 서버 값 그대로 — 번호·상태는 서버가 줄 때(무역·관리자)만 보인다. 확정 가능 여부를 목록이 판정하지 않는다.
// 조회는 전 역할, 등록 버튼은 무역·관리자(서버가 최종).

import { useState } from "react";
import { Link } from "react-router";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { IntakeCsvUploadPanel } from "../components/intake-csv-upload";
import { toKstDisplay } from "../lib/datetime";
import {
  INTAKE_STATUS_FILTERS,
  ORDER_INTAKES_QUERY_KEY,
  intakeBadgeClass,
  intakeStatusLabel,
  poOccupiedText,
  type IntakeSummary,
} from "../lib/order-intake";
import { usePagedList, usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";

interface UserLookup {
  id: number;
  display_name: string;
}

function filterQuery(filters: { status: string; q: string; assignee: string }) {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.assignee) params.set("assignee_id", filters.assignee);
  if (filters.q.trim()) params.set("q", filters.q.trim());
  return params.toString();
}

export function IntakeStatusBadge({ status }: { status: string }) {
  return <span className={`cell-nowrap rounded border px-2 py-0.5 text-xs ${intakeBadgeClass(status)}`}>{intakeStatusLabel(status)}</span>;
}

export function OrderIntakeListPage() {
  const { me } = useSession();
  // 등록(수동·CSV)·양식은 무역·관리자만(서버 계약 — hasRole은 ADMIN 포함). 403을 받으면 래치해 CSV 조작을 거둔다.
  const [csvForbidden, setCsvForbidden] = useState(false);
  const canWrite = hasRole(me, "TRADE");
  const canCsv = canWrite && !csvForbidden;
  const [csvOpen, setCsvOpen] = useState(false);
  const [filters, setFilters] = useState({ status: "", q: "", assignee: "" });
  // 담당자 목록(활성 사용자 조회)은 무역·관리자 전용 API다 — 그 밖의 역할은 호출하지 않는다(403 소음 방지).
  const users = usePagedQuery<UserLookup>(["users", "lookup"], "/v1/users/lookup?size=200", canWrite);
  const userItems = users.data?.items ?? [];

  const query = filterQuery(filters);
  // ★ 쿼리 키에 필터를 넣는다 — usePagedList의 키는 경로를 자동으로 포함하지 않는다.
  const list = usePagedList<IntakeSummary>(
    [...ORDER_INTAKES_QUERY_KEY, "list", query],
    query ? `/v1/order-intakes?${query}` : "/v1/order-intakes",
  );
  const nameOf = (id: number) => userItems.find((u) => u.id === id)?.display_name ?? `담당자 #${id}`;

  return (
    <section>
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">주문 접수 (오더 인테이크)</h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            바이어 PO를 받아 검토하는 곳입니다. 대기 상태에서 품번 매핑을 확인하고 확정하면 수주(접수 상태)가 만들어지고, 거부하면 사유와 함께 보존됩니다.
          </p>
        </div>
        {canWrite && (
          <div className="flex flex-wrap gap-2">
            {canCsv && (
              <button
                type="button"
                aria-expanded={csvOpen}
                aria-controls="intake-csv-upload"
                onClick={() => setCsvOpen((open) => !open)}
                className="cell-nowrap rounded border border-gray-900 px-4 py-2 text-sm"
              >
                {csvOpen ? "CSV 업로드 닫기" : "CSV 업로드"}
              </button>
            )}
            <Link to="/orders/intakes/new" className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white">
              수동 등록
            </Link>
          </div>
        )}
      </header>
      {/* 패널은 늘 마운트하고 숨기기만 한다 — 닫았다 열어도 결과·재시도 상태가 남고, 토글의 aria-controls가 항상 실재 id를 가리킨다. */}
      {canWrite && (
        <div id="intake-csv-upload" hidden={!csvOpen && !csvForbidden}>
          <IntakeCsvUploadPanel onForbidden={() => setCsvForbidden(true)} />
        </div>
      )}

      <form role="search" aria-label="인테이크 필터" className="mt-4 flex flex-wrap items-end gap-3 text-sm" onSubmit={(event) => event.preventDefault()}>
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">상태</span>
          <select
            name="status"
            value={filters.status}
            onChange={(event) => setFilters({ ...filters, status: event.target.value })}
            className="rounded border border-gray-300 px-3 py-2"
          >
            <option value="">전체</option>
            {INTAKE_STATUS_FILTERS.map((code) => (
              <option key={code} value={code}>
                {intakeStatusLabel(code)}
              </option>
            ))}
          </select>
        </label>
        {canWrite && (
          <label className="flex flex-col gap-1">
            <span className="cell-nowrap text-gray-600">담당자</span>
            <select
              name="assignee"
              value={filters.assignee}
              onChange={(event) => setFilters({ ...filters, assignee: event.target.value })}
              className="rounded border border-gray-300 px-3 py-2"
            >
              <option value="">전체</option>
              {userItems.map((user) => (
                <option key={user.id} value={user.id}>
                  {user.display_name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">바이어 PO번호·바이어명</span>
          <input
            name="q"
            value={filters.q}
            maxLength={100}
            onChange={(event) => setFilters({ ...filters, q: event.target.value })}
            className="rounded border border-gray-300 px-3 py-2"
          />
        </label>
      </form>
      {canWrite && users.data && users.data.total > userItems.length && (
        <p role="status" className="mt-1 text-xs text-gray-500">
          담당자 {users.data.total}명 중 {userItems.length}명만 필터에 표시합니다.
        </p>
      )}

      <ListPager data={list.data} page={list.page} onPageChange={list.setPage} className="mt-4" />

      <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint={query ? "조건에 맞는 인테이크가 없습니다. 필터를 바꿔 보세요." : "아직 접수된 오더 인테이크가 없습니다."}
        >
          <table className="w-full text-sm">
            <caption className="sr-only">오더 인테이크 목록</caption>
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th scope="col" className="cell-nowrap px-4 py-2 text-center">바이어 PO번호</th>
                <th scope="col" className="cell-nowrap px-4 py-2 text-center">상태</th>
                <th scope="col" className="cell-nowrap px-4 py-2">바이어</th>
                <th scope="col" className="cell-nowrap px-4 py-2 text-center">시장</th>
                <th scope="col" className="cell-nowrap px-4 py-2 text-center">라인</th>
                <th scope="col" className="cell-nowrap px-4 py-2 text-center">합계</th>
                <th scope="col" className="cell-nowrap px-4 py-2">담당자</th>
                <th scope="col" className="cell-nowrap px-4 py-2 text-center">접수일시</th>
                <th scope="col" className="cell-nowrap px-4 py-2">비고</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((row) => (
                <tr key={row.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2 text-center">
                    <Link to={`/orders/intakes/${row.id}`} className="underline">
                      {row.buyer_po_no}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-center">
                    <IntakeStatusBadge status={row.status} />
                  </td>
                  <td className="break-keep px-4 py-2">{row.buyer_name ?? "—"}</td>
                  <td className="cell-nowrap px-4 py-2 text-center">{row.dest_market_code}</td>
                  <td className="num cell-nowrap px-4 py-2">{row.line_count}</td>
                  <td className="num cell-nowrap px-4 py-2">
                    {row.total_text} {row.currency}
                  </td>
                  <td className="cell-nowrap px-4 py-2">{nameOf(row.assignee_id)}</td>
                  <td className="num cell-nowrap px-4 py-2">{toKstDisplay(row.created_at)}</td>
                  <td className="break-keep px-4 py-2">
                    <span className="flex flex-col gap-1">
                      {row.po_occupied !== null && (
                        <span className="cell-nowrap w-fit rounded border border-dashed border-signal-red px-2 py-0.5 text-xs text-signal-red" title={poOccupiedText(row.po_occupied)}>
                          PO 점유됨 — 확정 막힘
                        </span>
                      )}
                      {row.po_occupied !== null && <span className="text-xs text-gray-600">{poOccupiedText(row.po_occupied)}</span>}
                      {row.copied_from_so_id !== null && <span className="text-xs text-gray-600">복제 재접수(원본 수주 #{row.copied_from_so_id})</span>}
                      {row.sales_order_id !== null && (
                        <Link to={`/sales-orders/${row.sales_order_id}`} className="cell-nowrap text-xs underline">
                          생성된 수주 보기
                        </Link>
                      )}
                    </span>
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
