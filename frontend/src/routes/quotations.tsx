// 견적 목록 (S3-1 PR-5b — design-A QT 화면 / G-01 전표 화면 규약).
//
// 규약: 한국어 break-keep · 좁은 셀·헤더 nowrap · 숫자 가운데 정렬 · 바이어는 SearchSelect · 금액은 서버 문자열 그대로(산술 0).
// 화면이 쓰기 버튼을 감추는 것은 편의다 — 역할·소유권은 서버가 다시 막는다(§18.1).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { useDialogBehavior } from "../components/confirm-dialog";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { SearchSelect } from "../components/search-select";
import { apiFetch } from "../lib/api";
import { errorMessage } from "../lib/api-errors";
import { quotationStatusLabel, statusBadgeClass } from "../lib/doc-status";
import { downloadFile } from "../lib/download";
import { useCurrencies } from "../lib/money";
import { usePagedList, usePagedQuery } from "../lib/paging";
import type { QuotationDetail, QuotationSummary } from "../lib/quotation";
import { QUOTATIONS_QUERY_KEY } from "../lib/quotation";
import { hasRole, useSession } from "../lib/session";
import type { Partner } from "./partners";
import type { Market } from "./markets";

export const QT_STATUS_FILTERS = ["DRAFT", "ISSUED", "CONVERTED", "EXPIRED", "CANCELLED"] as const;

/** 목록·CSV가 같은 필터 문자열을 쓴다(CSV는 지금 보는 조건 그대로 내려받는다). */
function filterQuery(filters: { status: string; q: string; dateFrom: string; dateTo: string }) {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.q.trim()) params.set("q", filters.q.trim());
  if (filters.dateFrom) params.set("date_from", filters.dateFrom);
  if (filters.dateTo) params.set("date_to", filters.dateTo);
  return params.toString();
}

export function QuotationStatusBadge({ status, lapsed = false }: { status: string; lapsed?: boolean }) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      <span
        className={`cell-nowrap rounded border px-2 py-0.5 text-xs ${statusBadgeClass(status)}`}
      >
        {quotationStatusLabel(status)}
      </span>
      {lapsed && status === "ISSUED" && (
        <span className="cell-nowrap rounded border border-signal-red px-2 py-0.5 text-xs text-signal-red">
          유효기간 경과
        </span>
      )}
    </span>
  );
}

export function QuotationListPage() {
  const { me } = useSession();
  const canWrite = hasRole(me, "TRADE");
  const [filters, setFilters] = useState({ status: "", q: "", dateFrom: "", dateTo: "" });
  const [creating, setCreating] = useState(false);
  const [csvError, setCsvError] = useState<string | null>(null);

  const query = filterQuery(filters);
  // ★ 쿼리 키에 필터를 넣는다 — usePagedList의 키는 경로를 자동으로 포함하지 않는다(안 넣으면 필터를 바꿔도 옛 결과가 남는다).
  const list = usePagedList<QuotationSummary>(
    [...QUOTATIONS_QUERY_KEY, "list", query],
    query ? `/v1/quotations?${query}` : "/v1/quotations",
  );

  async function exportCsv() {
    setCsvError(null);
    try {
      await downloadFile(
        query ? `/v1/quotations/export.csv?${query}` : "/v1/quotations/export.csv",
        "견적목록.csv",
      );
    } catch (caught) {
      setCsvError(errorMessage(caught, "내려받지 못했습니다."));
    }
  }

  return (
    <section>
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">견적</h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            바이어에게 내는 견적입니다. 발행하면 가격·조건이 동결되고, 고치려면 개정본을 만듭니다.
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
            <button
              type="button"
              onClick={() => setCreating(true)}
              className="cell-nowrap rounded bg-gray-900 px-3 py-2 text-sm text-white"
            >
              견적 작성
            </button>
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
        aria-label="견적 필터"
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
            {QT_STATUS_FILTERS.map((code) => (
              <option key={code} value={code}>
                {quotationStatusLabel(code)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">견적번호·바이어명</span>
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
              ? "조건에 맞는 견적이 없습니다. 필터를 바꿔 보세요."
              : "아직 견적이 없습니다. '견적 작성'으로 첫 견적을 만드세요."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="cell-nowrap px-4 py-2">견적번호</th>
                <th className="cell-nowrap px-4 py-2 text-center">증빙일</th>
                <th className="cell-nowrap px-4 py-2 text-center">상태</th>
                <th className="cell-nowrap px-4 py-2">바이어</th>
                <th className="cell-nowrap px-4 py-2 text-center">목적지</th>
                <th className="cell-nowrap px-4 py-2 text-center">합계</th>
                <th className="cell-nowrap px-4 py-2 text-center">유효기간</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((row) => (
                <tr key={row.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">
                    <Link to={`/quotations/${row.id}`} className="underline">
                      {row.doc_number}
                    </Link>
                  </td>
                  <td className="num cell-nowrap px-4 py-2">{row.doc_date}</td>
                  <td className="px-4 py-2 text-center">
                    <QuotationStatusBadge status={row.status} lapsed={row.is_lapsed} />
                  </td>
                  <td className="break-keep px-4 py-2">{row.buyer_name}</td>
                  <td className="cell-nowrap px-4 py-2 text-center">{row.dest_market_code}</td>
                  <td className="num cell-nowrap px-4 py-2">
                    {row.total_text} {row.currency}
                  </td>
                  <td className="num cell-nowrap px-4 py-2">{row.valid_until ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>

      {creating && <CreateQuotationDialog onClose={() => setCreating(false)} />}
    </section>
  );
}

/** 견적 작성(DRAFT) — 필수 3값(바이어·목적지·통화)만 받고, 나머지는 상세에서 채운다. */
function CreateQuotationDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const currencies = useCurrencies();
  const markets = usePagedQuery<Market>(["markets", "select"], "/v1/markets?size=200");
  const [buyer, setBuyer] = useState<Partner | null>(null);
  const [market, setMarket] = useState("");
  const [currency, setCurrency] = useState("");
  const boxRef = useRef<HTMLFormElement | null>(null);
  useDialogBehavior(boxRef, onClose);
  // 멱등 키는 다이얼로그를 여는 순간 1개 — 실패 뒤 재시도(더블클릭 포함)는 같은 키, 입력이 바뀌면(=요청 본문이 달라지면) 새 키.
  const lock = useRef(false);
  const keyRef = useRef(crypto.randomUUID());
  const rekey = () => {
    keyRef.current = crypto.randomUUID();
  };

  const create = useMutation({
    mutationFn: () =>
      apiFetch<QuotationDetail>("/v1/quotations", {
        method: "POST",
        idempotencyKey: keyRef.current,
        body: {
          buyer_partner_id: buyer?.id,
          dest_market_code: market,
          currency,
        },
      }),
    onSuccess: (created) => {
      void client.invalidateQueries({ queryKey: QUOTATIONS_QUERY_KEY });
      void navigate(`/quotations/${created.id}`);
    },
  });

  const ready = buyer !== null && market !== "" && currency !== "";

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4">
      <form
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-label="견적 작성"
        onSubmit={(event) => {
          event.preventDefault();
          if (!ready || lock.current) return;
          lock.current = true; // 동기 잠금 — isPending은 한 틱 늦게 켜진다
          create.mutate(undefined, { onSettled: () => (lock.current = false) });
        }}
        className="w-full max-w-md rounded-lg bg-white p-5 shadow-lg"
      >
        <h2 className="text-lg font-bold">견적 작성</h2>
        <p className="mt-1 break-keep text-sm text-gray-500">
          초안이 만들어지고 견적번호가 붙습니다. 통화는 라인을 넣기 전까지만 바꿀 수 있습니다.
        </p>
        <div className="mt-4 flex flex-col gap-3 text-sm">
          <div className="flex flex-col gap-1">
            <span className="text-gray-600">바이어</span>
            <SearchSelect<Partner>
              label="바이어"
              path="/v1/partners"
              params={{ type: "BUYER" }}
              queryKey={["partners"]}
              value={buyer}
              onChange={(next) => {
                rekey();
                setBuyer(next);
              }}
              getKey={(item) => item.id}
              getLabel={(item) => `${item.name_ko} (${item.partner_code})`}
            />
          </div>
          <label className="flex flex-col gap-1">
            <span className="text-gray-600">목적지 시장</span>
            <select
              value={market}
              onChange={(event) => {
                rekey();
                setMarket(event.target.value);
              }}
              className="rounded border border-gray-300 px-3 py-2"
            >
              <option value="">선택하세요</option>
              {markets.data?.items.map((item) => (
                <option key={item.code} value={item.code}>
                  {item.name_ko} ({item.code})
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-gray-600">통화</span>
            <select
              value={currency}
              onChange={(event) => {
                rekey();
                setCurrency(event.target.value);
              }}
              className="rounded border border-gray-300 px-3 py-2"
            >
              <option value="">선택하세요</option>
              {currencies.data?.items.map((item) => (
                <option key={item.code} value={item.code}>
                  {item.code}
                </option>
              ))}
            </select>
          </label>
        </div>
        {create.error && (
          <p role="alert" className="mt-3 break-keep text-sm text-signal-red">
            {errorMessage(create.error, "견적을 만들지 못했습니다.")}
          </p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm"
          >
            닫기
          </button>
          <button
            type="submit"
            disabled={!ready || create.isPending}
            className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {create.isPending ? "만드는 중…" : "초안 만들기"}
          </button>
        </div>
      </form>
    </div>
  );
}
