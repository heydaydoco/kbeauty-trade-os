// PI 상세 (S3-1 PR-6b — design-A PI 화면 / design-B B8 전이 UI / G-01·G-03).
//
// 규칙(서버가 정본, 화면은 편의 — §18.1):
// - PI는 생성=발행=동결이다 — 가격·조건·라인·은행 스냅샷은 늘 읽기 전용. 편집 UI는 FREE(내부 메모·담당자) `/meta`뿐이다.
// - 모든 쓰기는 화면이 본 기준 version(baseVersion)을 싣는다(낙관 잠금). 서버 version이 앞서가면 "다른 곳에서 수정" 배너+불러오기.
// - 취소는 사유 필수 확인 다이얼로그 — 멱등 키는 다이얼로그를 여는 순간 1개, 더블클릭은 동기 잠금(ref)으로 1회만 전송.
// - 합계·라인 금액·선수금 청구액은 서버 문자열만 그대로 표시한다. 프런트 산술 0.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ConfirmDialog } from "../components/confirm-dialog";
import { DocumentFlowPanel } from "../components/document-flow-panel";
import {
  AdvanceView,
  BankSnapshotView,
  DocField,
  EMPTY,
  incotermText,
  paymentTermsText,
  show,
} from "../components/proforma-facts";
import { StatusTimeline } from "../components/status-timeline";
import { ApiError, apiFetch } from "../lib/api";
import { errorMessage, isVersionConflict } from "../lib/api-errors";
import {
  PRICE_BASIS_LABEL,
  canCancelProforma,
  canCreateSalesOrderFromProforma,
  proformaStatusLabel,
} from "../lib/doc-status";
import { usePagedQuery } from "../lib/paging";
import {
  PROFORMAS_QUERY_KEY,
  proformaDetailKey,
  type ProformaDetail,
} from "../lib/proforma";
import { QUOTATIONS_QUERY_KEY } from "../lib/quotation";
import { DOCUMENT_FLOW_QUERY_KEY, SALES_ORDERS_QUERY_KEY } from "../lib/sales-order";
import { hasRole, useSession } from "../lib/session";
import { ProformaStatusBadge } from "./proformas";
import { SalesOrderCreateDialog } from "./sales-order-create";

interface UserLookup {
  id: number;
  display_name: string;
}

const NOUN = "PI";

/** PI가 바뀌면 화면 상태 전체를 새로 시작한다 — 이전 PI의 기준 version·폼이 남지 않게. */
export function ProformaDetailPage() {
  const params = useParams();
  return <ProformaDetailView key={params.proformaId} />;
}

function ProformaDetailView() {
  const params = useParams();
  const id = Number(params.proformaId);
  const { me } = useSession();
  const canWrite = hasRole(me, "TRADE");
  const client = useQueryClient();

  const detailKey = proformaDetailKey(id);
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: () => apiFetch<ProformaDetail>(`/v1/proforma-invoices/${id}`),
    enabled: Number.isInteger(id) && id > 0,
    // 편집 화면은 항상 서버의 최신 version을 기준으로 한다.
    staleTime: 0,
  });

  const [cancelling, setCancelling] = useState(false);
  const [creatingSo, setCreatingSo] = useState(false);
  const [notice, setNotice] = useState<unknown>(null);
  const [resetToken, setResetToken] = useState(0);
  // ★ 화면이 "마지막으로 본/내가 쓴" version — 창 포커스 재조회로 서버 version이 앞서가도 쓰기는 이 값으로 보낸다
  //   (옛 화면으로 다른 사람의 수정을 덮어쓰지 않게 서버 409가 막는다). 내 쓰기·'최신 내용 불러오기'로만 갱신.
  const [baseVersion, setBaseVersion] = useState<number | null>(null);
  const submitLock = useRef(false);
  const actionKey = useRef("");
  const lastSent = useRef<string | null>(null);

  function invalidateAll() {
    void client.invalidateQueries({ queryKey: PROFORMAS_QUERY_KEY });
    // 취소는 부모 견적 상태를 수렴시킨다(견적 화면도 최신으로).
    void client.invalidateQueries({ queryKey: QUOTATIONS_QUERY_KEY });
    void client.invalidateQueries({ queryKey: DOCUMENT_FLOW_QUERY_KEY });
    void client.invalidateQueries({ queryKey: SALES_ORDERS_QUERY_KEY });
  }

  function afterWrite(next: ProformaDetail) {
    client.setQueryData(detailKey, next);
    setBaseVersion(next.version);
    setNotice(null);
    invalidateAll();
  }

  function reload() {
    setNotice(null);
    cancel.reset();
    setCancelling(false);
    setCreatingSo(false);
    void client.invalidateQueries({ queryKey: DOCUMENT_FLOW_QUERY_KEY });
    // 재조회가 끝난 뒤에 기준 version·폼을 새로 시드한다(옛 캐시로 시드하면 곧바로 또 어긋난다).
    void detail.refetch().then((result) => {
      if (result.data) setBaseVersion(result.data.version);
      setResetToken((value) => value + 1);
    });
  }

  const cancel = useMutation({
    mutationFn: (input: { version: number; reason: string }) =>
      apiFetch<ProformaDetail>(`/v1/proforma-invoices/${id}/transitions`, {
        method: "POST",
        idempotencyKey: actionKey.current,
        body: { to: "CANCELLED", version: input.version, reason: input.reason },
      }),
    onSuccess: (result) => {
      setCancelling(false);
      afterWrite(result);
      setResetToken((value) => value + 1);
    },
  });

  /** 동기 잠금 — mutation의 isPending은 한 틱 늦게 켜져 같은 틱의 두 번째 클릭이 새어 나간다. */
  function submitCancel(input: { version: number; reason: string }) {
    if (submitLock.current) return;
    submitLock.current = true;
    // 사유·version이 바뀌면 본문이 달라진다(멱등 request_body에 포함) — 그때만 새 키. 같은 본문 재시도는 같은 키.
    const serialized = JSON.stringify(input);
    if (lastSent.current !== null && lastSent.current !== serialized) {
      actionKey.current = crypto.randomUUID();
    }
    lastSent.current = serialized;
    cancel.mutate(input, { onSettled: () => (submitLock.current = false) });
  }

  function openCancel() {
    actionKey.current = crypto.randomUUID();
    lastSent.current = null;
    setCancelling(true);
  }

  const loadedVersion = detail.data?.version;
  useEffect(() => {
    if (baseVersion === null && loadedVersion !== undefined) setBaseVersion(loadedVersion);
  }, [baseVersion, loadedVersion]);

  if (detail.isPending) return <p className="p-5 text-gray-500">불러오는 중…</p>;
  if (detail.error || !detail.data) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <section>
        <p role="alert" className="break-keep text-signal-red">
          {notFound ? "PI를 찾을 수 없습니다." : errorMessage(detail.error, "PI를 불러오지 못했습니다.", NOUN)}
        </p>
        <Link to="/proforma-invoices" className="mt-3 inline-block text-sm underline">
          PI 목록으로
        </Link>
      </section>
    );
  }

  const pi = detail.data;
  const base = baseVersion ?? pi.version;
  const stale = pi.version !== base;

  return (
    <section>
      <Link to="/proforma-invoices" className="cell-nowrap text-sm text-gray-500 underline">
        ← PI 목록
      </Link>
      <header className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex flex-wrap items-center gap-3 text-2xl font-bold">
            <span className="cell-nowrap">{pi.doc_number}</span>
            <ProformaStatusBadge status={pi.status} lapsed={pi.is_lapsed} />
          </h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            발행·동결된 청구서입니다. 가격·조건·라인·은행 정보는 고칠 수 없고(내부 메모·담당자만 수정 가능), 잘못되었으면
            취소하고 견적에서 새로 만듭니다.
          </p>
          <p className="mt-1 text-sm text-gray-500">
            원천 견적:{" "}
            <Link to={`/quotations/${pi.qt_id}`} className="underline">
              {pi.qt_doc_number ?? `#${pi.qt_id}`}
            </Link>
            {pi.copied_from_id !== null && (
              <>
                {" · "}복제 원본:{" "}
                <Link to={`/proforma-invoices/${pi.copied_from_id}`} className="underline">
                  #{pi.copied_from_id}
                </Link>
              </>
            )}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={reload}
            className="cell-nowrap rounded border border-gray-300 px-3 py-2 text-sm"
          >
            최신 내용 불러오기
          </button>
          {canWrite && canCreateSalesOrderFromProforma(pi.status) && (
            <button
              type="button"
              onClick={() => setCreatingSo(true)}
              disabled={stale || (pi.is_lapsed && pi.status === "ISSUED")}
              title={
                pi.is_lapsed && pi.status === "ISSUED"
                  ? "유효기간이 지난 PI로는 수주를 만들 수 없습니다."
                  : stale
                    ? "다른 곳에서 수정되었습니다. 먼저 '최신 내용 불러오기'를 누르세요."
                    : undefined
              }
              className="cell-nowrap rounded border border-gray-900 px-3 py-2 text-sm disabled:opacity-50"
            >
              SO 만들기
            </button>
          )}
          {canWrite && canCancelProforma(pi.status) && (
            <button
              type="button"
              onClick={openCancel}
              className="cell-nowrap rounded border border-signal-red px-3 py-2 text-sm text-signal-red"
            >
              취소
            </button>
          )}
        </div>
      </header>

      {notice !== null && (
        <div role="alert" className="mt-4 rounded border border-signal-red p-3 text-sm text-signal-red">
          <p className="break-keep">{errorMessage(notice, undefined, NOUN)}</p>
          {isVersionConflict(notice) && (
            <button
              type="button"
              onClick={reload}
              className="cell-nowrap mt-2 rounded border border-signal-red px-3 py-1"
            >
              최신 내용 불러오기
            </button>
          )}
        </div>
      )}

      {stale && (
        <div role="status" className="mt-4 rounded border border-gray-400 p-3 text-sm">
          <p className="break-keep">
            다른 곳에서 이 PI가 수정되었습니다. 아래 내용은 최신이지만 메모 편집 폼은 이전 내용입니다. 저장하면 충돌(409)로
            거절됩니다.
          </p>
          <button
            type="button"
            onClick={reload}
            className="cell-nowrap mt-2 rounded border border-gray-400 px-3 py-1"
          >
            최신 내용 불러오기
          </button>
        </div>
      )}

      <div className="mt-6 grid gap-6">
        <section aria-label="PI 헤더" className="rounded-lg border border-gray-200 p-4">
          <h2 className="text-lg font-semibold">PI 정보</h2>
          <dl className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <DocField label="바이어">{pi.buyer_name}</DocField>
            <DocField label="목적지">{pi.dest_market_code}</DocField>
            <DocField label="통화">{pi.currency}</DocField>
            <DocField label="증빙일">{pi.doc_date}</DocField>
            <DocField label="유효기간">{pi.valid_until}</DocField>
            <DocField label="환율">
              {pi.fx_rate === null ? EMPTY : `${pi.fx_rate} (기준일 ${show(pi.fx_rate_date)})`}
              {pi.fx_rate_age_days !== null && pi.fx_rate_age_days > 0 && (
                <span className="ml-2 text-gray-500">{pi.fx_rate_age_days}일 지남</span>
              )}
            </DocField>
            <DocField label="결제조건">{paymentTermsText(pi.payment_terms)}</DocField>
            <DocField label="인코텀즈">{incotermText(pi.incoterm)}</DocField>
            <DocField label="바이어 주소">{show(pi.buyer_address)}</DocField>
          </dl>
          {pi.advance !== null && (
            <div className="mt-4 border-t border-gray-100 pt-4">
              <AdvanceView advance={pi.advance} currency={pi.currency} />
            </div>
          )}
        </section>

        <BankSnapshotView bank={pi.bank} />

        {canWrite && (
          <MetaPanel
            key={`meta-${pi.id}-${resetToken}`}
            pi={{ ...pi, version: base }}
            onSaved={afterWrite}
            onError={setNotice}
          />
        )}
        {!canWrite && (
          <p className="break-keep text-sm text-gray-600">내부 메모: {show(pi.internal_note)}</p>
        )}

        <section aria-labelledby="pi-lines-title">
          <h2 id="pi-lines-title" className="text-lg font-semibold">
            라인
          </h2>
          <div className="mt-2 overflow-x-auto rounded-lg border border-gray-200">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-gray-600">
                <tr>
                  <th className="cell-nowrap px-3 py-2 text-center">번호</th>
                  <th className="cell-nowrap px-3 py-2">SKU</th>
                  <th className="cell-nowrap px-3 py-2">품명</th>
                  <th className="cell-nowrap px-3 py-2 text-center">수량</th>
                  <th className="cell-nowrap px-3 py-2 text-center">단가</th>
                  <th className="cell-nowrap px-3 py-2 text-center">기준</th>
                  <th className="cell-nowrap px-3 py-2 text-center">금액</th>
                </tr>
              </thead>
              <tbody>
                {pi.lines.map((line) => (
                  <tr key={line.id} className="border-t border-gray-100 align-top">
                    <td className="num cell-nowrap px-3 py-2">{line.line_no}</td>
                    <td className="cell-nowrap px-3 py-2">{line.sku_code}</td>
                    <td className="break-keep px-3 py-2">
                      {line.sku_name_ko}
                      {line.buyer_item_code && (
                        <span className="block text-xs text-gray-500">바이어 품번 {line.buyer_item_code}</span>
                      )}
                      {line.price_reason && (
                        <span className="block text-xs text-gray-500">사유: {line.price_reason}</span>
                      )}
                    </td>
                    <td className="num cell-nowrap px-3 py-2">{line.quantity}</td>
                    <td className="num cell-nowrap px-3 py-2">{line.is_free ? "무상" : line.unit_price_text}</td>
                    <td className="cell-nowrap px-3 py-2 text-center">
                      {PRICE_BASIS_LABEL[line.price_basis] ?? line.price_basis}
                    </td>
                    <td className="num cell-nowrap px-3 py-2">{line.line_amount_text}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-gray-200 bg-gray-50 font-semibold">
                  <td colSpan={6} className="cell-nowrap px-3 py-2 text-right">
                    합계 (서버 계산)
                  </td>
                  <td className="num cell-nowrap px-3 py-2">
                    {pi.total_text} {pi.currency}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </section>

        <DocumentFlowPanel kind="PROFORMA_INVOICE" id={pi.id} />

        <StatusTimeline
          basePath={`/v1/proforma-invoices/${pi.id}`}
          queryKey={detailKey}
          statusLabel={proformaStatusLabel}
        />
      </div>

      {creatingSo && canWrite && canCreateSalesOrderFromProforma(pi.status) && (
        <SalesOrderCreateDialog
          source={{
            kind: "PROFORMA_INVOICE",
            id: pi.id,
            doc_number: pi.doc_number,
            doc_date: pi.doc_date,
            lines: pi.lines.map((line) => ({
              id: line.id,
              line_no: line.line_no,
              sku_code: line.sku_code,
              sku_name_ko: line.sku_name_ko,
              quantity: line.quantity,
            })),
          }}
          version={base}
          onClose={() => setCreatingSo(false)}
          onReload={reload}
        />
      )}
      {cancelling && (
        <ConfirmDialog
          title="PI를 취소할까요?"
          danger
          confirmLabel="취소 확정"
          reasonLabel="취소 사유 (필수)"
          description={
            <p>
              취소하면 <strong>되돌릴 수 없습니다.</strong> PI번호는 남고 다시 쓰이지 않으며, 이 PI가 가져간 견적 수량은
              잔량으로 돌아갑니다. 입금이 붙었거나 이어진 문서가 살아 있으면 취소되지 않습니다.
            </p>
          }
          pending={cancel.isPending}
          error={cancel.error ? errorMessage(cancel.error, undefined, NOUN) : null}
          onReload={isVersionConflict(cancel.error) ? reload : undefined}
          onCancel={() => {
            cancel.reset();
            setCancelling(false);
          }}
          onConfirm={(reason) => submitCancel({ version: base, reason })}
        />
      )}
    </section>
  );
}

// ── FREE 열(내부 메모·담당자) — 동결 후에도 편집 ────────────────────────────

const inputClass = "rounded border border-gray-300 px-3 py-2 text-sm";

function MetaPanel({
  pi,
  onSaved,
  onError,
}: {
  pi: ProformaDetail;
  onSaved: (next: ProformaDetail) => void;
  onError: (error: unknown) => void;
}) {
  const users = usePagedQuery<UserLookup>(["users", "lookup"], "/v1/users/lookup?size=200");
  const [note, setNote] = useState(pi.internal_note ?? "");
  const [assignee, setAssignee] = useState(String(pi.assignee_id));
  const lock = useRef(false);

  const save = useMutation({
    mutationFn: () =>
      apiFetch<ProformaDetail>(`/v1/proforma-invoices/${pi.id}/meta`, {
        method: "PATCH",
        body: {
          version: pi.version,
          internal_note: note.trim() === "" ? null : note.trim(),
          assignee_id: Number(assignee),
        },
      }),
    onSuccess: onSaved,
    onError,
  });

  const items = users.data?.items ?? [];
  const known = items.some((user) => String(user.id) === assignee);
  // 바뀐 게 없으면 저장하지 않는다 — 불필요한 version 증가가 다른 화면의 409를 부른다.
  const changed = note.trim() !== (pi.internal_note ?? "") || assignee !== String(pi.assignee_id);

  return (
    <form
      aria-label="내부 메모·담당자"
      className="rounded-lg border border-gray-200 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (lock.current || !changed) return;
        lock.current = true;
        save.mutate(undefined, { onSettled: () => (lock.current = false) });
      }}
    >
      <h2 className="text-lg font-semibold">내부 메모·담당자</h2>
      <p className="mt-1 break-keep text-sm text-gray-500">
        바이어에게 나가지 않는 항목입니다. 동결된 뒤에도 고칠 수 있습니다.
      </p>
      <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">담당자</span>
          <select value={assignee} onChange={(e) => setAssignee(e.target.value)} className={inputClass}>
            {!known && <option value={assignee}>사용자 #{assignee}</option>}
            {items.map((user) => (
              <option key={user.id} value={user.id}>
                {user.display_name}
              </option>
            ))}
          </select>
          {users.data && users.data.total > items.length && (
            <span role="status" className="text-xs text-gray-500">
              {users.data.total}명 중 {items.length}명만 표시합니다.
            </span>
          )}
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">내부 메모</span>
          <textarea
            value={note}
            maxLength={1000}
            rows={2}
            onChange={(e) => setNote(e.target.value)}
            className={inputClass}
          />
        </label>
      </div>
      <button
        type="submit"
        disabled={save.isPending || !changed}
        className="cell-nowrap mt-3 rounded border border-gray-300 px-4 py-2 text-sm disabled:opacity-50"
      >
        {save.isPending ? "저장 중…" : "메모·담당자 저장"}
      </button>
    </form>
  );
}
