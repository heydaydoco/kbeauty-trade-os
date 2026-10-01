// 발주(PO) 상세 (S3-1 PR-8b — design-A A12 / design-B 전이 UI / design-F F5 / G-01·G-03 / ADR-0057 원가 마스킹).
//
// 규칙(서버가 정본, 화면은 편의 — §18.1):
// - PO는 생성=발행=동결이다 — 가격·조건·라인은 늘 읽기 전용. 편집 UI는 FREE(내부 메모·담당자, 공급사 확인 상태의 OC 일자·참조) `/meta`뿐이고
//   취소된 발주는 그것도 닫는다.
// - 전이는 공급사 확인(OC — OC 일자 필수)·취소(사유 필수) 두 가지뿐이고 `POST …/transitions`로 간다. 자동 전이는 없다.
// - 모든 쓰기는 화면이 본 기준 version(baseVersion)을 싣는다(낙관 잠금). 서버 version이 앞서가면 "다른 곳에서 수정" 배너+불러오기.
// - 확인 다이얼로그의 멱등 키는 여는 순간 1개 — 새 키는 본문이 실제로 달라질 때만, 더블클릭은 동기 잠금(ref)으로 1회만 전송, 409는 다이얼로그 안에서 재조회.
// - ★ 원가 마스킹: 서버 응답에 원가 키(통화·합계·단가·라인금액·기준·환율)가 없을 수 있다(조회 전용 역할). 키가 있을 때만 그리고, 없으면 열·칸 자체를 만들지 않는다.
//   원가 값은 상태·URL·로컬 스토리지·콘솔에 따로 보관·출력하지 않는다. 자유 텍스트(내부 메모·취소 사유·OC 참조) 옆에 원가 금지 안내를 둔다.
// - 합계·라인 금액은 서버 문자열만 그대로 표시한다. 프런트 산술 0.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ConfirmDialog, useDialogBehavior } from "../components/confirm-dialog";
import { DocField, EMPTY, incotermText, paymentTermsText, show } from "../components/proforma-facts";
import { StatusTimeline } from "../components/status-timeline";
import { ApiError, apiFetch } from "../lib/api";
import { errorMessage, isVersionConflict } from "../lib/api-errors";
import {
  PO_KIND_LABEL,
  PRICE_BASIS_LABEL,
  canCancelPurchaseOrder,
  canConfirmPurchaseOrder,
  canEditPurchaseOrderMeta,
  canEditPurchaseOrderOc,
  purchaseOrderStatusLabel,
} from "../lib/doc-status";
import { usePagedQuery } from "../lib/paging";
import {
  NO_COST_IN_FREE_TEXT,
  PURCHASE_ORDERS_QUERY_KEY,
  hasCost,
  purchaseOrderDetailKey,
  type PurchaseOrderDetail,
} from "../lib/purchase-order";
import { hasRole, useSession } from "../lib/session";
import { PurchaseOrderStatusBadge } from "./purchase-orders";

interface UserLookup {
  id: number;
  display_name: string;
}

const NOUN = "발주";
const inputClass = "rounded border border-gray-300 px-3 py-2 text-sm";

type Action = "confirm" | "cancel";

interface TransitionInput {
  to: "SUPPLIER_CONFIRMED" | "CANCELLED";
  version: number;
  reason?: string;
  oc_received_on?: string;
  oc_reference?: string;
}

/** 발주가 바뀌면 화면 상태 전체를 새로 시작한다 — 이전 발주의 기준 version·폼이 남지 않게. */
export function PurchaseOrderDetailPage() {
  const params = useParams();
  return <PurchaseOrderDetailView key={params.purchaseOrderId} />;
}

function PurchaseOrderDetailView() {
  const params = useParams();
  const id = Number(params.purchaseOrderId);
  const { me } = useSession();
  const canWrite = hasRole(me, "TRADE");
  const client = useQueryClient();

  const detailKey = purchaseOrderDetailKey(id);
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: () => apiFetch<PurchaseOrderDetail>(`/v1/purchase-orders/${id}`),
    enabled: Number.isInteger(id) && id > 0,
    // 편집 화면은 항상 서버의 최신 version을 기준으로 한다.
    staleTime: 0,
  });

  const [action, setAction] = useState<Action | null>(null);
  const [notice, setNotice] = useState<unknown>(null);
  const [resetToken, setResetToken] = useState(0);
  // ★ 화면이 "마지막으로 본/내가 쓴" version — 창 포커스 재조회로 서버 version이 앞서가도 쓰기는 이 값으로 보낸다
  //   (옛 화면으로 다른 사람의 수정을 덮어쓰지 않게 서버 409가 막는다). 내 쓰기·'최신 내용 불러오기'로만 갱신.
  const [baseVersion, setBaseVersion] = useState<number | null>(null);
  const submitLock = useRef(false);
  const actionKey = useRef("");
  const lastSent = useRef<string | null>(null);

  function afterWrite(next: PurchaseOrderDetail) {
    client.setQueryData(detailKey, next);
    setBaseVersion(next.version);
    setNotice(null);
    void client.invalidateQueries({ queryKey: PURCHASE_ORDERS_QUERY_KEY });
  }

  function reload() {
    setNotice(null);
    transition.reset();
    setAction(null);
    // 재조회가 끝난 뒤에 기준 version·폼을 새로 시드한다(옛 캐시로 시드하면 곧바로 또 어긋난다).
    void detail.refetch().then((result) => {
      if (result.data) setBaseVersion(result.data.version);
      setResetToken((value) => value + 1);
    });
  }

  const transition = useMutation({
    mutationFn: (input: TransitionInput) =>
      apiFetch<PurchaseOrderDetail>(`/v1/purchase-orders/${id}/transitions`, {
        method: "POST",
        idempotencyKey: actionKey.current,
        body: input,
      }),
    onSuccess: (result) => {
      setAction(null);
      afterWrite(result);
      setResetToken((value) => value + 1);
    },
  });

  /** 동기 잠금 — mutation의 isPending은 한 틱 늦게 켜져 같은 틱의 두 번째 클릭이 새어 나간다. */
  function submitTransition(input: TransitionInput) {
    if (submitLock.current) return;
    submitLock.current = true;
    // 사유·OC 값·version이 바뀌면 본문이 달라진다(멱등 request_body에 포함) — 그때만 새 키. 같은 본문 재시도는 같은 키.
    const serialized = JSON.stringify(input);
    if (lastSent.current !== null && lastSent.current !== serialized) {
      actionKey.current = crypto.randomUUID();
    }
    lastSent.current = serialized;
    transition.mutate(input, { onSettled: () => (submitLock.current = false) });
  }

  function openAction(next: Action) {
    actionKey.current = crypto.randomUUID();
    lastSent.current = null;
    transition.reset();
    setAction(next);
  }

  function closeAction() {
    if (transition.isPending) return;
    transition.reset();
    setAction(null);
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
          {notFound ? "발주를 찾을 수 없습니다." : errorMessage(detail.error, "발주를 불러오지 못했습니다.", NOUN)}
        </p>
        <Link to="/purchase-orders" className="mt-3 inline-block text-sm underline">
          발주 목록으로
        </Link>
      </section>
    );
  }

  const po = detail.data;
  const base = baseVersion ?? po.version;
  const stale = po.version !== base;
  const showCost = hasCost(po);
  // 409(낙관 잠금 충돌뿐 아니라 그 사이 상태가 바뀐 전이 충돌 포함)는 다이얼로그 안에서 재조회할 수 있게 한다.
  const transitionConflict = transition.error instanceof ApiError && transition.error.status === 409;

  return (
    <section>
      <Link to="/purchase-orders" className="cell-nowrap text-sm text-gray-500 underline">
        ← 발주 목록
      </Link>
      <header className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex flex-wrap items-center gap-3 text-2xl font-bold">
            <span className="cell-nowrap">{po.doc_number}</span>
            <PurchaseOrderStatusBadge status={po.status} />
          </h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            발행·동결된 발주서입니다. 가격·조건·라인은 고칠 수 없고(내부 메모·담당자·OC 기록만 수정 가능), 잘못되었으면 취소하고
            새로 만듭니다.
          </p>
          {po.copied_from_id !== null && (
            <p className="mt-1 text-sm text-gray-500">
              복제 원본:{" "}
              <Link to={`/purchase-orders/${po.copied_from_id}`} className="underline">
                #{po.copied_from_id}
              </Link>
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={reload}
            className="cell-nowrap rounded border border-gray-300 px-3 py-2 text-sm"
          >
            최신 내용 불러오기
          </button>
          {canWrite && canConfirmPurchaseOrder(po.status) && (
            <button
              type="button"
              onClick={() => openAction("confirm")}
              disabled={stale}
              title={stale ? "다른 곳에서 수정되었습니다. 먼저 '최신 내용 불러오기'를 누르세요." : undefined}
              className="cell-nowrap rounded border border-gray-900 px-3 py-2 text-sm disabled:opacity-50"
            >
              공급사 확인(OC)
            </button>
          )}
          {canWrite && canCancelPurchaseOrder(po.status) && (
            <button
              type="button"
              onClick={() => openAction("cancel")}
              disabled={stale}
              title={stale ? "다른 곳에서 수정되었습니다. 먼저 '최신 내용 불러오기'를 누르세요." : undefined}
              className="cell-nowrap rounded border border-signal-red px-3 py-2 text-sm text-signal-red disabled:opacity-50"
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
            다른 곳에서 이 발주가 수정되었습니다. 아래 내용은 최신이지만 메모 편집 폼은 이전 내용입니다. 저장하면 충돌(409)로
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
        <section aria-label="발주 헤더" className="rounded-lg border border-gray-200 p-4">
          <h2 className="text-lg font-semibold">발주 정보</h2>
          <dl className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <DocField label="공급사">{po.supplier_name}</DocField>
            <DocField label="구분">{PO_KIND_LABEL[po.po_kind] ?? po.po_kind}</DocField>
            <DocField label="증빙일">{po.doc_date}</DocField>
            {po.currency !== undefined && <DocField label="통화">{po.currency}</DocField>}
            {po.fx_rate !== undefined && (
              <DocField label="환율">
                {po.fx_rate === null ? EMPTY : `${po.fx_rate} (기준일 ${show(po.fx_rate_date)})`}
              </DocField>
            )}
            <DocField label="결제조건">{paymentTermsText(po.payment_terms)}</DocField>
            <DocField label="인코텀즈">{incotermText(po.incoterm)}</DocField>
            <DocField label="OC 일자">{show(po.oc_received_on)}</DocField>
            <DocField label="OC 참조">{show(po.oc_reference)}</DocField>
          </dl>
        </section>

        {canWrite && canEditPurchaseOrderMeta(po.status) ? (
          <MetaPanel
            key={`meta-${po.id}-${resetToken}`}
            po={po}
            version={base}
            onSaved={afterWrite}
            onError={setNotice}
          />
        ) : (
          <p className="break-keep text-sm text-gray-600">내부 메모: {show(po.internal_note)}</p>
        )}

        <section aria-labelledby="po-lines-title">
          <h2 id="po-lines-title" className="text-lg font-semibold">
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
                  <th className="cell-nowrap px-3 py-2 text-center">요청납기</th>
                  {showCost && <th className="cell-nowrap px-3 py-2 text-center">단가</th>}
                  {showCost && <th className="cell-nowrap px-3 py-2 text-center">기준</th>}
                  {showCost && <th className="cell-nowrap px-3 py-2 text-center">금액</th>}
                </tr>
              </thead>
              <tbody>
                {po.lines.map((line) => (
                  <tr key={line.id} className="border-t border-gray-100 align-top">
                    <td className="num cell-nowrap px-3 py-2">{line.line_no}</td>
                    <td className="cell-nowrap px-3 py-2">{line.sku_code}</td>
                    <td className="break-keep px-3 py-2">{line.sku_name_ko}</td>
                    <td className="num cell-nowrap px-3 py-2">{line.quantity}</td>
                    <td className="num cell-nowrap px-3 py-2">{line.requested_delivery_date ?? EMPTY}</td>
                    {showCost && <td className="num cell-nowrap px-3 py-2">{show(line.unit_cost_text)}</td>}
                    {showCost && (
                      <td className="cell-nowrap px-3 py-2 text-center">
                        {line.price_basis === undefined ? EMPTY : (PRICE_BASIS_LABEL[line.price_basis] ?? line.price_basis)}
                      </td>
                    )}
                    {showCost && <td className="num cell-nowrap px-3 py-2">{show(line.line_cost_text)}</td>}
                  </tr>
                ))}
              </tbody>
              {showCost && (
                <tfoot>
                  <tr className="border-t border-gray-200 bg-gray-50 font-semibold">
                    <td colSpan={7} className="cell-nowrap px-3 py-2 text-right">
                      합계 (서버 계산)
                    </td>
                    <td className="num cell-nowrap px-3 py-2">
                      {po.total_text} {po.currency}
                    </td>
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        </section>

        <StatusTimeline
          basePath={`/v1/purchase-orders/${po.id}`}
          queryKey={detailKey}
          statusLabel={purchaseOrderStatusLabel}
        />
      </div>

      {action === "confirm" && canWrite && canConfirmPurchaseOrder(po.status) && (
        <OcDialog
          docDate={po.doc_date}
          pending={transition.isPending}
          error={transition.error ? errorMessage(transition.error, undefined, NOUN) : null}
          conflict={transitionConflict}
          onReload={reload}
          onCancel={closeAction}
          onConfirm={(ocDate, ocReference) =>
            submitTransition({
              to: "SUPPLIER_CONFIRMED",
              version: base,
              oc_received_on: ocDate,
              ...(ocReference !== "" ? { oc_reference: ocReference } : {}),
            })
          }
        />
      )}
      {action === "cancel" && canWrite && canCancelPurchaseOrder(po.status) && (
        <ConfirmDialog
          title="발주를 취소할까요?"
          danger
          confirmLabel="취소 확정"
          reasonLabel="취소 사유 (필수)"
          reasonHint={NO_COST_IN_FREE_TEXT}
          description={
            <p>
              취소하면 <strong>되돌릴 수 없습니다.</strong> 발주번호는 남고 다시 쓰이지 않습니다. 이미 공급사에 나간 발주라면
              공급사에도 따로 알려 주세요.
            </p>
          }
          pending={transition.isPending}
          error={transition.error ? errorMessage(transition.error, undefined, NOUN) : null}
          onReload={transitionConflict ? reload : undefined}
          onCancel={closeAction}
          onConfirm={(reason) => submitTransition({ to: "CANCELLED", version: base, reason })}
        />
      )}
    </section>
  );
}

// ── 공급사 확인(OC) 다이얼로그 — OC 일자 필수 ──────────────────────────────

function OcDialog({
  docDate,
  pending,
  error,
  conflict,
  onReload,
  onCancel,
  onConfirm,
}: {
  docDate: string;
  pending: boolean;
  error: string | null;
  conflict: boolean;
  onReload: () => void;
  onCancel: () => void;
  onConfirm: (ocDate: string, ocReference: string) => void;
}) {
  const titleId = useId();
  const boxRef = useRef<HTMLDivElement | null>(null);
  const dateRef = useRef<HTMLInputElement | null>(null);
  const [ocDate, setOcDate] = useState("");
  const [ocReference, setOcReference] = useState("");
  useDialogBehavior(boxRef, onCancel, dateRef);
  const blocked = pending || ocDate === "";

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4">
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-md rounded-lg bg-white p-5 shadow-lg"
      >
        <h2 id={titleId} className="text-lg font-bold">
          공급사 확인(OC) 기록
        </h2>
        <p className="mt-2 break-keep text-sm text-gray-700">
          공급사가 발주를 확인(Order Confirmation)해 회신한 사실을 기록합니다. OC 일자는 발주 증빙일({docDate}) 이후, 오늘
          이전이어야 합니다.
        </p>
        <div className="mt-4 flex flex-col gap-3 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-gray-600">OC 일자 (필수)</span>
            <input
              ref={dateRef}
              type="date"
              value={ocDate}
              min={docDate}
              onChange={(e) => setOcDate(e.target.value)}
              className={inputClass}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-gray-600">OC 참조 (선택)</span>
            <input
              value={ocReference}
              maxLength={100}
              onChange={(e) => setOcReference(e.target.value)}
              className={inputClass}
            />
            <span className="break-keep text-xs text-signal-red">{NO_COST_IN_FREE_TEXT}</span>
          </label>
        </div>
        {error && (
          <p role="alert" className="mt-3 break-keep text-sm text-signal-red">
            {error}
          </p>
        )}
        {error && conflict && (
          <button
            type="button"
            onClick={onReload}
            className="cell-nowrap mt-2 rounded border border-gray-300 px-3 py-1 text-sm"
          >
            최신 내용 불러오기
          </button>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={pending}
            className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm disabled:opacity-50"
          >
            닫기
          </button>
          <button
            type="button"
            disabled={blocked}
            onClick={() => onConfirm(ocDate, ocReference.trim())}
            className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {pending ? "처리 중…" : "공급사 확인 기록"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── FREE 열(내부 메모·담당자·OC) — 동결 후에도 편집(취소 전까지) ─────────────────

function MetaPanel({
  po,
  version,
  onSaved,
  onError,
}: {
  po: PurchaseOrderDetail;
  version: number;
  onSaved: (next: PurchaseOrderDetail) => void;
  onError: (error: unknown) => void;
}) {
  const users = usePagedQuery<UserLookup>(["users", "lookup"], "/v1/users/lookup?size=200");
  const [note, setNote] = useState(po.internal_note ?? "");
  const [assignee, setAssignee] = useState(String(po.assignee_id));
  const [ocDate, setOcDate] = useState(po.oc_received_on ?? "");
  const [ocReference, setOcReference] = useState(po.oc_reference ?? "");
  const [formError, setFormError] = useState<string | null>(null);
  const lock = useRef(false);
  const editOc = canEditPurchaseOrderOc(po.status);

  const noteChanged = note.trim() !== (po.internal_note ?? "");
  const assigneeChanged = assignee !== String(po.assignee_id);
  const ocDateChanged = editOc && ocDate !== (po.oc_received_on ?? "");
  const ocRefChanged = editOc && ocReference.trim() !== (po.oc_reference ?? "");
  // 바뀐 게 없으면 저장하지 않는다 — 불필요한 version 증가가 다른 화면의 409를 부른다.
  const changed = noteChanged || assigneeChanged || ocDateChanged || ocRefChanged;

  const save = useMutation({
    mutationFn: () => {
      // 바뀐 열만 보낸다 — OC 열은 공급사 확인 상태에서만 서버가 받는다(그 밖에서 보내면 422).
      const body: Record<string, unknown> = { version };
      if (noteChanged) body.internal_note = note.trim() === "" ? null : note.trim();
      if (assigneeChanged) body.assignee_id = Number(assignee);
      if (ocDateChanged) body.oc_received_on = ocDate;
      if (ocRefChanged) body.oc_reference = ocReference.trim() === "" ? null : ocReference.trim();
      return apiFetch<PurchaseOrderDetail>(`/v1/purchase-orders/${po.id}/meta`, { method: "PATCH", body });
    },
    onSuccess: onSaved,
    onError,
  });

  const items = users.data?.items ?? [];
  const known = items.some((user) => String(user.id) === assignee);

  return (
    <form
      aria-label="내부 메모·담당자"
      className="rounded-lg border border-gray-200 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (lock.current || !changed || save.isPending) return;
        if (editOc && ocDate === "") {
          setFormError("OC 일자는 비울 수 없습니다.");
          return;
        }
        setFormError(null);
        lock.current = true;
        save.mutate(undefined, { onSettled: () => (lock.current = false) });
      }}
    >
      <h2 className="text-lg font-semibold">내부 메모·담당자{editOc ? "·OC 기록" : ""}</h2>
      <p className="mt-1 break-keep text-sm text-gray-500">
        공급사에 나가지 않는 항목입니다. 동결된 뒤에도 고칠 수 있습니다(취소 전까지).
        {!editOc && " OC 일자·참조는 ‘공급사 확인(OC)’으로 기록합니다."}
      </p>
      <fieldset disabled={save.isPending} className="contents">
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">담당자</span>
            <select
              value={assignee}
              onChange={(e) => setAssignee(e.target.value)}
              className={inputClass}
            >
              {!known && <option value={assignee}>사용자 #{assignee}</option>}
              {items.map((user) => (
                <option key={user.id} value={user.id}>
                  {user.display_name}
                </option>
              ))}
            </select>
            <span className="break-keep text-xs text-gray-500">
              무역·관리자 역할 사용자만 담당자가 될 수 있습니다(서버가 확인합니다).
            </span>
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
            <span className="break-keep text-xs text-signal-red">{NO_COST_IN_FREE_TEXT}</span>
          </label>
          {editOc && (
            <>
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-gray-600">OC 일자</span>
                <input
                  type="date"
                  value={ocDate}
                  min={po.doc_date}
                  onChange={(e) => setOcDate(e.target.value)}
                  className={inputClass}
                />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-gray-600">OC 참조</span>
                <input
                  value={ocReference}
                  maxLength={100}
                  onChange={(e) => setOcReference(e.target.value)}
                  className={inputClass}
                />
                <span className="break-keep text-xs text-signal-red">{NO_COST_IN_FREE_TEXT}</span>
              </label>
            </>
          )}
        </div>
      </fieldset>
      {formError && (
        <p role="alert" className="mt-2 break-keep text-sm text-signal-red">
          {formError}
        </p>
      )}
      <button
        type="submit"
        disabled={save.isPending || !changed}
        className="cell-nowrap mt-3 rounded border border-gray-300 px-4 py-2 text-sm disabled:opacity-50"
      >
        {save.isPending ? "저장 중…" : "저장"}
      </button>
    </form>
  );
}
