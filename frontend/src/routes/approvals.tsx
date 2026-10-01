// 승인 — 결재함·내 요청 (S3-1 PR-9b — design-C C7·C8 / G-01 전표 화면 규약).
//
// - 결재함 = 서버가 '내가 결정할 수 있다'고 판정한 REQUESTED(알림과 무관한 서버 계산 — 알림이 늦어도 뜬다). 오래된 요청이 위.
// - 내 요청 = 내가 기안한 승인 전 상태. 상태 필터는 서버 쿼리로 나간다.
// - 결정(승인·반려·회수)은 상세에서만 한다 — 근거(스냅샷)를 보지 않고 목록에서 누르는 결재를 만들지 않는다.
// - 규약: 한국어 break-keep · 좁은 셀·헤더 nowrap · 숫자 가운데 정렬 · 금액은 서버 정수의 자릿수 옮김뿐(산술 0).

import { useState } from "react";
import { Link } from "react-router";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { toKstDisplay } from "../lib/datetime";
import {
  APPROVALS_QUERY_KEY,
  APPROVAL_STATUS_FILTERS,
  approvalBadgeClass,
  approvalStatusLabel,
  approvalTargetRoute,
  approvalTypeLabel,
  approverRoleLabel,
  moneyText,
  type ApprovalView,
} from "../lib/approval";
import { useCurrencies } from "../lib/money";
import { usePagedList } from "../lib/paging";
import { useSession } from "../lib/session";

export function ApprovalStatusBadge({ status }: { status: string }) {
  return (
    <span className={`cell-nowrap rounded border px-2 py-0.5 text-xs ${approvalBadgeClass(status)}`}>
      {approvalStatusLabel(status)}
    </span>
  );
}

type Tab = "inbox" | "mine";

export function ApprovalsPage() {
  const [tab, setTab] = useState<Tab>("inbox");
  const [status, setStatus] = useState("");
  const { me } = useSession();
  const currencies = useCurrencies();

  const query = new URLSearchParams({ scope: tab });
  if (tab === "mine" && status) query.set("status", status);
  const path = `/v1/approvals?${query.toString()}`;
  // ★ 결재함은 늘 신선해야 한다(staleTime 0) — 다른 사람이 먼저 결재한 건이 남아 있으면 누른 뒤에야 안다.
  const list = usePagedList<ApprovalView>([...APPROVALS_QUERY_KEY, "list", tab, status], path, true, {
    staleTime: 0,
  });

  /** 대결 수임 표시 — 화면 안내일 뿐이다(결정 가능 여부는 서버가 준다): 내 역할이 아닌데 결재할 수 있으면 대결이다. */
  const delegated = (row: ApprovalView) =>
    tab === "inbox" &&
    row.can_decide &&
    me !== null &&
    !me.roles.includes("ADMIN") &&
    !me.roles.includes(row.required_role);

  return (
    <section>
      <header>
        <h1 className="text-2xl font-bold">승인</h1>
        <p className="mt-1 break-keep text-sm text-gray-500">
          결재함에는 내가 결정할 수 있는 요청이 오래된 순으로 보입니다. 본인이 요청한 승인은 직접 결정할 수 없습니다.
        </p>
      </header>

      <div role="tablist" aria-label="승인 구분" className="mt-4 flex gap-2 border-b border-gray-200">
        {(
          [
            ["inbox", "결재함"],
            ["mine", "내 요청"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`cell-nowrap -mb-px border-b-2 px-4 py-2 text-sm ${
              tab === key ? "border-gray-900 font-semibold" : "border-transparent text-gray-500"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "mine" && (
        <form
          role="search"
          aria-label="내 요청 필터"
          className="mt-4 flex flex-wrap items-end gap-3 text-sm"
          onSubmit={(event) => event.preventDefault()}
        >
          <label className="flex flex-col gap-1">
            <span className="cell-nowrap text-gray-600">상태</span>
            <select
              name="status"
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              className="rounded border border-gray-300 px-3 py-2"
            >
              <option value="">전체</option>
              {APPROVAL_STATUS_FILTERS.map((code) => (
                <option key={code} value={code}>
                  {approvalStatusLabel(code)}
                </option>
              ))}
            </select>
          </label>
        </form>
      )}

      <ListPager data={list.data} page={list.page} onPageChange={list.setPage} className="mt-4" />

      <div role="tabpanel" className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint={
            tab === "inbox"
              ? "결재할 요청이 없습니다. 새 요청이 오면 여기에 표시됩니다."
              : "요청한 승인이 없습니다. 여신 한도를 넘는 수주를 확정하려 할 때 승인을 요청합니다."
          }
        >
          <table className="w-full min-w-[56rem] text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="cell-nowrap px-3 py-2">승인</th>
                <th className="cell-nowrap px-3 py-2">유형</th>
                <th className="cell-nowrap px-3 py-2">대상</th>
                <th className="cell-nowrap px-3 py-2">요청자</th>
                <th className="cell-nowrap px-3 py-2">요청일</th>
                <th className="cell-nowrap px-3 py-2 text-center">승인 기준 금액</th>
                <th className="cell-nowrap px-3 py-2">결재 역할</th>
                <th className="cell-nowrap px-3 py-2">상태</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((row) => {
                const target = approvalTargetRoute(row.target_type, row.target_id);
                return (
                  <tr key={row.id} className="border-t border-gray-100 align-top">
                    <td className="cell-nowrap px-3 py-2">
                      <Link to={`/approvals/${row.id}`} className="underline">
                        {`승인 #${row.id}`}
                      </Link>
                    </td>
                    <td className="break-keep px-3 py-2">{approvalTypeLabel(row.approval_type)}</td>
                    <td className="px-3 py-2">
                      {target === null ? (
                        row.target_label
                      ) : (
                        <Link to={target} className="cell-nowrap underline">
                          {row.target_label}
                        </Link>
                      )}
                    </td>
                    <td className="cell-nowrap px-3 py-2">{row.requester_name ?? "알 수 없음"}</td>
                    <td className="cell-nowrap px-3 py-2">{row.created_at ? toKstDisplay(row.created_at) : "—"}</td>
                    <td className="cell-nowrap num px-3 py-2 text-center">
                      {moneyText(row.basis_amount, row.basis_currency, currencies.data?.items)}
                    </td>
                    <td className="cell-nowrap px-3 py-2">
                      {approverRoleLabel(row.required_role)}
                      {delegated(row) && <span className="ml-1 text-xs text-gray-500">(대결)</span>}
                    </td>
                    <td className="px-3 py-2">
                      <ApprovalStatusBadge status={row.status} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </ListState>
      </div>
    </section>
  );
}
