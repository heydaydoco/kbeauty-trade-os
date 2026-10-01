// 수주(SO) 상세·편집 (S3-1 PR-7b — design-A SO 화면 / design-B B8 전이 UI / G-01·G-03·G-11).
//
// 규칙(서버가 정본, 화면은 편의 — §18.1):
// - 접수(RECEIVED)만 CONTENT(헤더 조건·라인) 편집. 보류·확정 이후·취소는 읽기 전용, FREE(내부 메모·담당자)만 /meta로 편집.
//   편집 불가로 바뀌면 열려 있던 헤더 폼·라인 수정 폼·제외 다이얼로그도 닫는다.
// - 모든 쓰기는 화면이 본 기준 version(baseVersion)을 싣는다(낙관 잠금). 서버 version이 앞서가면 "다른 곳에서 수정" 배너+불러오기.
// - 보류·재개·취소는 확인 다이얼로그 — 멱등 키는 다이얼로그를 여는 순간 1개(본문이 달라질 때만 새 키), 더블클릭은 동기 잠금(ref)으로 1회만 전송.
// - 합계·라인 금액·원천 단가는 서버 문자열만 그대로 표시한다. 프런트 산술 0. 편집은 바뀐 필드만 보내고 무변경 저장은 막는다.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";
import { ConfirmDialog } from "../components/confirm-dialog";
import { DocumentFlowPanel } from "../components/document-flow-panel";
import { GatePanel } from "../components/gate-panel";
import { DocField, EMPTY, incotermText, paymentTermsText, show } from "../components/proforma-facts";
import { SearchSelect } from "../components/search-select";
import { StatusTimeline } from "../components/status-timeline";
import { ApiError, apiFetch } from "../lib/api";
import { isVersionConflict } from "../lib/api-errors";
import { todayKst } from "../lib/datetime";
import {
  BALANCE_ANCHOR_LABEL,
  INCOTERM_CODES,
  PAYMENT_TYPE_LABEL,
  PRICE_BASIS_LABEL,
  canCancelSalesOrder,
  canEditSalesOrder,
  canHoldSalesOrder,
  canResumeSalesOrder,
  salesOrderStatusLabel,
} from "../lib/doc-status";
import { usePagedQuery } from "../lib/paging";
import { PROFORMAS_QUERY_KEY } from "../lib/proforma";
import { QUOTATIONS_QUERY_KEY } from "../lib/quotation";
import {
  DOCUMENT_FLOW_QUERY_KEY,
  SALES_ORDERS_QUERY_KEY,
  salesOrderDetailKey,
  type SalesOrderDetail,
  type SalesOrderLine,
  type SoLineMutation,
} from "../lib/sales-order";
import { occupantNotice, soErrorMessage } from "../lib/sales-order-errors";
import { QUANTITY_EXCEEDS_OPEN_CODE } from "../lib/proforma";
import { hasRole, useSession } from "../lib/session";
import type { Market } from "./markets";
import { SalesOrderStatusBadge, SourceLinks } from "./sales-orders";
import type { Sku } from "./skus";

interface UserLookup {
  id: number;
  display_name: string;
}

const NOUN = "수주";
const inputClass = "rounded border border-gray-300 px-3 py-2 text-sm";
const MAX_QUANTITY = 99_999_999;

/** 빈 문자열 → null (서버는 null을 "값 지우기"로 읽는다). */
const orNull = (value: string): string | null => (value.trim() === "" ? null : value.trim());

type Action = "hold" | "resume" | "cancel";
type Transition = { to: "ON_HOLD" | "RECEIVED" | "CONFIRMED" | "CANCELLED"; version: number; reason: string | null };

/** 수주가 바뀌면 화면 상태 전체를 새로 시작한다 — 이전 수주의 기준 version·폼이 남지 않게. */
export function SalesOrderDetailPage() {
  const params = useParams();
  return <SalesOrderDetailView key={params.salesOrderId} />;
}

function SalesOrderDetailView() {
  const params = useParams();
  const id = Number(params.salesOrderId);
  const { me } = useSession();
  const canWrite = hasRole(me, "TRADE");
  const client = useQueryClient();

  const detailKey = salesOrderDetailKey(id);
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: () => apiFetch<SalesOrderDetail>(`/v1/sales-orders/${id}`),
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

  function invalidateAll() {
    void client.invalidateQueries({ queryKey: SALES_ORDERS_QUERY_KEY });
    void client.invalidateQueries({ queryKey: DOCUMENT_FLOW_QUERY_KEY });
    // 취소는 부모 견적·PI 상태·잔량을 수렴시킨다(그 화면들도 최신으로).
    void client.invalidateQueries({ queryKey: QUOTATIONS_QUERY_KEY });
    void client.invalidateQueries({ queryKey: PROFORMAS_QUERY_KEY });
  }

  function afterWrite(next: SalesOrderDetail) {
    client.setQueryData(detailKey, next);
    setBaseVersion(next.version);
    setNotice(null);
    invalidateAll();
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
    void client.invalidateQueries({ queryKey: DOCUMENT_FLOW_QUERY_KEY });
  }

  const transition = useMutation({
    mutationFn: (input: Transition) =>
      apiFetch<SalesOrderDetail>(`/v1/sales-orders/${id}/transitions`, {
        method: "POST",
        idempotencyKey: actionKey.current,
        body: { to: input.to, version: input.version, reason: input.reason },
      }),
    onSuccess: (result) => {
      setAction(null);
      afterWrite(result);
      setResetToken((value) => value + 1);
    },
  });

  /** 동기 잠금 — mutation의 isPending은 한 틱 늦게 켜져 같은 틱의 두 번째 클릭이 새어 나간다. */
  function submitTransition(input: Transition) {
    if (submitLock.current) return;
    submitLock.current = true;
    // 사유·version이 바뀌면 본문이 달라진다(멱등 request_body에 포함) — 그때만 새 키. 같은 본문 재시도는 같은 키.
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

  const loadedVersion = detail.data?.version;
  useEffect(() => {
    // 첫 조회 값이 기준이다. 이후 서버 version이 앞서가도(창 포커스 재조회) 기준은 내 쓰기·불러오기로만 바뀐다.
    if (baseVersion === null && loadedVersion !== undefined) setBaseVersion(loadedVersion);
  }, [baseVersion, loadedVersion]);

  if (detail.isPending) return <p className="p-5 text-gray-500">불러오는 중…</p>;
  if (detail.error || !detail.data) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <section>
        <p role="alert" className="break-keep text-signal-red">
          {notFound ? "수주를 찾을 수 없습니다." : soErrorMessage(detail.error, "수주를 불러오지 못했습니다.", NOUN)}
        </p>
        <Link to="/sales-orders" className="mt-3 inline-block text-sm underline">
          수주 목록으로
        </Link>
      </section>
    );
  }

  const so = detail.data;
  const base = baseVersion ?? so.version;
  const stale = so.version !== base;
  const writeSo = { ...so, version: base }; // 쓰기용 — 폼이 시드된 기준 version
  const editable = canWrite && canEditSalesOrder(so.status);
  // 재개 목표는 서버가 confirmed_at으로 판정한다(확정 이력이 없으면 접수로, 있으면 확정으로).
  const resumeTo = so.confirmed_at === null ? "RECEIVED" : "CONFIRMED";

  return (
    <section>
      <Link to="/sales-orders" className="cell-nowrap text-sm text-gray-500 underline">
        ← 수주 목록
      </Link>
      <header className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex flex-wrap items-center gap-3 text-2xl font-bold">
            <span className="cell-nowrap">{so.doc_number}</span>
            <SalesOrderStatusBadge status={so.status} />
          </h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            {so.status === "RECEIVED" &&
              "접수 상태입니다. 바이어 PO·조건·라인을 고칠 수 있습니다(가격·조건은 원천에서 복사된 값입니다)."}
            {so.status === "ON_HOLD" && "보류 중입니다. 내용을 고치려면 먼저 재개하세요(내부 메모·담당자만 수정 가능)."}
            {so.status === "CONFIRMED" && "확정된 수주입니다. 가격·조건·라인은 동결되어 고칠 수 없습니다(내부 메모·담당자만 수정 가능)."}
            {so.status === "CANCELLED" && "취소된 수주입니다. 수주번호는 남고 다시 쓰이지 않으며, 읽기 전용입니다."}
          </p>
          <p className="mt-1 text-sm text-gray-500">
            원천: <SourceLinks so={so} />
            {so.copied_from_id !== null && (
              <>
                {" · "}복제 원본:{" "}
                <Link to={`/sales-orders/${so.copied_from_id}`} className="underline">
                  #{so.copied_from_id}
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
          {canWrite && canHoldSalesOrder(so.status) && (
            <button
              type="button"
              onClick={() => openAction("hold")}
              className="cell-nowrap rounded border border-gray-900 px-3 py-2 text-sm"
            >
              보류
            </button>
          )}
          {canWrite && canResumeSalesOrder(so.status) && (
            <button
              type="button"
              onClick={() => openAction("resume")}
              className="cell-nowrap rounded border border-gray-900 px-3 py-2 text-sm"
            >
              재개
            </button>
          )}
          {canWrite && canCancelSalesOrder(so.status) && (
            <button
              type="button"
              onClick={() => openAction("cancel")}
              className="cell-nowrap rounded border border-signal-red px-3 py-2 text-sm text-signal-red"
            >
              취소
            </button>
          )}
        </div>
      </header>

      {notice !== null && (
        <div role="alert" className="mt-4 rounded border border-signal-red p-3 text-sm text-signal-red">
          <p className="break-keep">{soErrorMessage(notice, undefined, NOUN)}</p>
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
            다른 곳에서 이 수주가 수정되었습니다. 아래 라인·합계는 최신이지만 편집 폼은 이전 내용입니다. 저장하면 충돌(409)로
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
        {editable ? (
          <HeaderEditor key={`${so.id}-${resetToken}`} so={writeSo} onSaved={afterWrite} />
        ) : (
          <HeaderReadOnly so={so} />
        )}

        {canWrite ? (
          <MetaPanel key={`meta-${so.id}-${resetToken}`} so={writeSo} onSaved={afterWrite} onError={setNotice} />
        ) : (
          <p className="break-keep text-sm text-gray-600">내부 메모: {show(so.internal_note)}</p>
        )}

        <LinesSection
          so={writeSo}
          editable={editable}
          detailKey={detailKey}
          onError={setNotice}
          onChanged={(version) => {
            setBaseVersion(version);
            setNotice(null);
            invalidateAll();
          }}
        />

        <GatePanel soId={so.id} soStatus={so.status} />

        <DocumentFlowPanel kind="SALES_ORDER" id={so.id} />

        <StatusTimeline
          basePath={`/v1/sales-orders/${so.id}`}
          queryKey={detailKey}
          statusLabel={salesOrderStatusLabel}
        />
      </div>

      {action === "hold" && canWrite && canHoldSalesOrder(so.status) && (
        <ConfirmDialog
          title="수주를 보류할까요?"
          confirmLabel="보류 확정"
          reasonLabel="보류 사유 (필수)"
          description={
            <p>
              보류하는 동안에는 조건·라인을 고칠 수 없습니다. 재개하면 보류 직전 상태({so.confirmed_at === null ? "접수" : "확정"})로
              돌아갑니다.
            </p>
          }
          pending={transition.isPending}
          error={transition.error ? soErrorMessage(transition.error, undefined, NOUN) : null}
          onReload={isVersionConflict(transition.error) ? reload : undefined}
          onCancel={() => {
            transition.reset();
            setAction(null);
          }}
          onConfirm={(reason) => submitTransition({ to: "ON_HOLD", version: base, reason })}
        />
      )}
      {action === "resume" && canWrite && canResumeSalesOrder(so.status) && (
        <ConfirmDialog
          title="수주를 재개할까요?"
          confirmLabel="재개"
          description={
            <p>
              보류 직전 상태인 <strong>{resumeTo === "RECEIVED" ? "접수" : "확정"}</strong>로 돌아갑니다.
            </p>
          }
          pending={transition.isPending}
          error={transition.error ? soErrorMessage(transition.error, undefined, NOUN) : null}
          onReload={isVersionConflict(transition.error) ? reload : undefined}
          onCancel={() => {
            transition.reset();
            setAction(null);
          }}
          onConfirm={() => submitTransition({ to: resumeTo, version: base, reason: null })}
        />
      )}
      {action === "cancel" && canWrite && canCancelSalesOrder(so.status) && (
        <ConfirmDialog
          title="수주를 취소할까요?"
          danger
          confirmLabel="취소 확정"
          reasonLabel="취소 사유 (필수)"
          description={
            <p>
              취소하면 <strong>되돌릴 수 없습니다.</strong> 수주번호는 남고 다시 쓰이지 않으며, 이 수주가 가져간 원천 수량은
              잔량으로 돌아가고 같은 바이어 PO번호를 다시 쓸 수 있습니다. 이어진 문서가 살아 있으면 취소되지 않습니다.
            </p>
          }
          pending={transition.isPending}
          error={transition.error ? soErrorMessage(transition.error, undefined, NOUN) : null}
          onReload={isVersionConflict(transition.error) ? reload : undefined}
          onCancel={() => {
            transition.reset();
            setAction(null);
          }}
          onConfirm={(reason) => submitTransition({ to: "CANCELLED", version: base, reason })}
        />
      )}
    </section>
  );
}

// ── 읽기 전용 헤더(보류·확정 이후·취소) ─────────────────────────────────────

function HeaderReadOnly({ so }: { so: SalesOrderDetail }) {
  return (
    <section aria-label="수주 헤더" className="rounded-lg border border-gray-200 p-4">
      <h2 className="text-lg font-semibold">수주 정보</h2>
      <dl className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <DocField label="바이어">{so.buyer_name}</DocField>
        <DocField label="바이어 PO번호">{show(so.buyer_po_no)}</DocField>
        <DocField label="바이어 PO 일자">{show(so.buyer_po_date)}</DocField>
        <DocField label="목적지">{so.dest_market_code}</DocField>
        <DocField label="통화">{so.currency}</DocField>
        <DocField label="증빙일">{so.doc_date}</DocField>
        <DocField label="환율">
          {so.fx_rate === null ? EMPTY : `${so.fx_rate} (기준일 ${show(so.fx_rate_date)})`}
          {so.fx_rate_age_days !== null && so.fx_rate_age_days > 0 && (
            <span className="ml-2 text-gray-500">{so.fx_rate_age_days}일 지남</span>
          )}
        </DocField>
        <DocField label="결제조건">{paymentTermsText(so.payment_terms)}</DocField>
        <DocField label="인코텀즈">{incotermText(so.incoterm)}</DocField>
        {so.confirmed_at !== null && <DocField label="확정 시각">{so.confirmed_at}</DocField>}
      </dl>
    </section>
  );
}

// ── 접수 헤더 편집 — 바뀐 필드만 보낸다(무변경 저장 차단) ──────────────────────

interface HeaderForm {
  doc_date: string;
  dest_market_code: string;
  fx_rate: string;
  fx_rate_date: string;
  payment_type: string;
  advance_pct: string;
  balance_anchor: string;
  balance_days: string;
  incoterm_code: string;
  incoterm_place: string;
  incoterm_year: string;
  buyer_name: string;
  buyer_po_no: string;
  buyer_po_date: string;
}

function toForm(so: SalesOrderDetail): HeaderForm {
  return {
    doc_date: so.doc_date,
    dest_market_code: so.dest_market_code,
    fx_rate: so.fx_rate ?? "",
    fx_rate_date: so.fx_rate_date ?? "",
    payment_type: so.payment_terms.payment_type ?? "",
    advance_pct: so.payment_terms.advance_pct ?? "",
    balance_anchor: so.payment_terms.balance_anchor ?? "",
    balance_days: so.payment_terms.balance_days === null ? "" : String(so.payment_terms.balance_days),
    incoterm_code: so.incoterm.code ?? "",
    incoterm_place: so.incoterm.place ?? "",
    incoterm_year: String(so.incoterm.year ?? 2020),
    buyer_name: so.buyer_name,
    buyer_po_no: so.buyer_po_no ?? "",
    buyer_po_date: so.buyer_po_date ?? "",
  };
}

const termsOf = (f: HeaderForm) =>
  f.payment_type === ""
    ? null
    : {
        payment_type: f.payment_type,
        advance_pct: orNull(f.advance_pct),
        balance_anchor: orNull(f.balance_anchor),
        balance_days: f.balance_days === "" ? null : Number(f.balance_days),
      };

/** 결제조건 입력 검증 — 서버 규칙(선수금 0.01~100 소수 2자리, 잔금 일수 정수 -90~365·음수는 선적예정일 기준만)과 같은 범위. 비운 칸은 의도적 null이다. */
export function termsError(f: Pick<HeaderForm, "payment_type" | "advance_pct" | "balance_anchor" | "balance_days">): string | null {
  if (f.payment_type === "") return null;
  const pct = f.advance_pct.trim();
  if (pct !== "") {
    if (!/^[0-9]{1,3}(\.[0-9]{1,2})?$/.test(pct) || Number(pct) < 0.01 || Number(pct) > 100) {
      return "선수금 비율은 0.01~100 사이 숫자(소수 2자리까지)로 입력해 주세요.";
    }
  }
  const days = f.balance_days.trim();
  if (days !== "") {
    if (!/^-?[0-9]+$/.test(days)) return "잔금 일수는 정수로 입력해 주세요(예: 30).";
    const n = Number(days);
    if (n < -90 || n > 365) return "잔금 일수는 -90~365 사이로 입력해 주세요.";
    if (n < 0 && f.balance_anchor !== "ETD_DATE") return "음수 잔금 일수는 잔금 기준이 선적예정일일 때만 쓸 수 있습니다.";
  }
  return null;
}

const incotermOf = (f: HeaderForm) =>
  f.incoterm_code === "" ? null : { code: f.incoterm_code, place: f.incoterm_place.trim(), year: Number(f.incoterm_year) };

/** 시드 대비 바뀐 필드만 본문으로 — 비어 있으면 저장할 것이 없다. */
function diffBody(so: SalesOrderDetail, seed: HeaderForm, f: HeaderForm): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (f.doc_date !== seed.doc_date) body.doc_date = f.doc_date;
  if (!so.is_reference && f.dest_market_code !== seed.dest_market_code) body.dest_market_code = f.dest_market_code;
  if (so.currency !== "KRW") {
    // KRW는 서버가 환율 1을 채운다 — 화면이 값을 만들지 않는다.
    if (f.fx_rate.trim() !== seed.fx_rate.trim()) body.fx_rate = orNull(f.fx_rate);
    if (f.fx_rate_date !== seed.fx_rate_date) body.fx_rate_date = orNull(f.fx_rate_date);
  }
  if (JSON.stringify(termsOf(f)) !== JSON.stringify(termsOf(seed))) body.payment_terms = termsOf(f);
  if (JSON.stringify(incotermOf(f)) !== JSON.stringify(incotermOf(seed))) body.incoterm = incotermOf(f);
  if (f.buyer_name.trim() !== seed.buyer_name.trim()) body.buyer_name = f.buyer_name.trim();
  if (f.buyer_po_no.trim() !== seed.buyer_po_no.trim()) body.buyer_po_no = orNull(f.buyer_po_no);
  if (f.buyer_po_date !== seed.buyer_po_date) body.buyer_po_date = orNull(f.buyer_po_date);
  return body;
}

function HeaderEditor({ so, onSaved }: { so: SalesOrderDetail; onSaved: (next: SalesOrderDetail) => void }) {
  const markets = usePagedQuery<Market>(["markets", "select"], "/v1/markets?size=200");
  const seed = useRef(toForm(so));
  const [f, setF] = useState<HeaderForm>(seed.current);
  const [localError, setLocalError] = useState<string | null>(null);
  const lock = useRef(false);
  const set = (key: keyof HeaderForm) => (value: string) => {
    setLocalError(null);
    setF((prev) => ({ ...prev, [key]: value }));
  };

  const body = diffBody(so, seed.current, f);
  const changed = Object.keys(body).length > 0;
  const docDateMissing = f.doc_date === "";
  const nameMissing = f.buyer_name.trim() === "";
  const termsProblem = termsError(f);

  const save = useMutation({
    mutationFn: () =>
      apiFetch<SalesOrderDetail>(`/v1/sales-orders/${so.id}`, {
        method: "PATCH",
        body: { version: so.version, ...body },
      }),
    onSuccess: (next) => {
      // 저장한 값이 새 기준이다 — 미저장 표시를 끈다.
      seed.current = f;
      onSaved(next);
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (lock.current || !changed || docDateMissing || nameMissing || termsProblem !== null) return;
    if (f.buyer_po_no.trim().length > 60) {
      setLocalError("바이어 PO번호는 60자 이하로 입력해 주세요.");
      return;
    }
    if (f.buyer_po_date !== "" && f.buyer_po_date > todayKst()) {
      setLocalError("바이어 PO 일자는 오늘보다 미래일 수 없습니다.");
      return;
    }
    lock.current = true; // 동기 잠금
    save.mutate(undefined, { onSettled: () => (lock.current = false) });
  }

  const occupant = occupantNotice(save.error);
  const conflict = isVersionConflict(save.error);
  const currencyIsKrw = so.currency === "KRW";

  return (
    <form aria-label="수주 헤더 편집" className="rounded-lg border border-gray-200 p-4" onSubmit={submit}>
      <h2 className="text-lg font-semibold">수주 정보 (접수 편집)</h2>
      <fieldset disabled={save.isPending} className="contents">
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">바이어</span>
            <span className="break-keep rounded border border-gray-200 bg-gray-50 px-3 py-2">{so.buyer_name}</span>
            <span className="text-xs text-gray-500">바이어·통화·원천은 바꿀 수 없습니다.</span>
          </div>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">바이어 PO번호</span>
            <input
              value={f.buyer_po_no}
              maxLength={200}
              onChange={(e) => set("buyer_po_no")(e.target.value)}
              className={inputClass}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">바이어 PO 일자</span>
            <input
              type="date"
              value={f.buyer_po_date}
              onChange={(e) => set("buyer_po_date")(e.target.value)}
              className={inputClass}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">증빙일 (필수)</span>
            <input type="date" value={f.doc_date} onChange={(e) => set("doc_date")(e.target.value)} className={inputClass} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">목적지 시장</span>
            <select
              value={f.dest_market_code}
              disabled={so.is_reference}
              onChange={(e) => set("dest_market_code")(e.target.value)}
              className={`${inputClass} disabled:bg-gray-100`}
            >
              {markets.data?.items.map((item) => (
                <option key={item.code} value={item.code}>
                  {item.name_ko} ({item.code})
                </option>
              ))}
              {!markets.data?.items.some((item) => item.code === f.dest_market_code) && (
                <option value={f.dest_market_code}>{f.dest_market_code}</option>
              )}
            </select>
            {so.is_reference && (
              <span className="break-keep text-xs text-gray-500">원천에서 만든 수주는 목적지를 바꿀 수 없습니다.</span>
            )}
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">서류상 바이어명 (필수)</span>
            <input
              value={f.buyer_name}
              maxLength={200}
              onChange={(e) => set("buyer_name")(e.target.value)}
              className={inputClass}
            />
          </label>
          {currencyIsKrw ? (
            <p className="break-keep text-sm text-gray-500 sm:col-span-2 lg:col-span-3">
              원화(KRW) 수주는 환율 1로 서버가 채웁니다.
            </p>
          ) : (
            <>
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-gray-600">환율</span>
                <input
                  inputMode="decimal"
                  value={f.fx_rate}
                  onChange={(e) => set("fx_rate")(e.target.value)}
                  className={`${inputClass} text-center`}
                />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-gray-600">환율 기준일</span>
                <input
                  type="date"
                  value={f.fx_rate_date}
                  onChange={(e) => set("fx_rate_date")(e.target.value)}
                  className={inputClass}
                />
                {so.fx_rate_age_days !== null && so.fx_rate_age_days > 0 && (
                  <span className="text-xs text-gray-500">저장된 기준일은 {so.fx_rate_age_days}일 지났습니다.</span>
                )}
              </label>
              <div className="hidden lg:block" />
            </>
          )}
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">결제조건</span>
            <select value={f.payment_type} onChange={(e) => set("payment_type")(e.target.value)} className={inputClass}>
              <option value="">선택 안 함</option>
              {Object.entries(PAYMENT_TYPE_LABEL)
                .filter(([code]) => code !== "LC" || so.payment_terms.payment_type === "LC")
                .map(([code, label]) => (
                  <option key={code} value={code}>
                    {label}
                  </option>
                ))}
            </select>
          </label>
          {f.payment_type !== "" && (
            <>
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-gray-600">선수금 비율(%)</span>
                <input
                  inputMode="decimal"
                  value={f.advance_pct}
                  onChange={(e) => set("advance_pct")(e.target.value)}
                  className={`${inputClass} text-center`}
                />
              </label>
              <div className="flex gap-2">
                <label className="flex flex-1 flex-col gap-1 text-sm">
                  <span className="cell-nowrap text-gray-600">잔금 기준</span>
                  <select
                    value={f.balance_anchor}
                    onChange={(e) => set("balance_anchor")(e.target.value)}
                    className={inputClass}
                  >
                    <option value="">없음</option>
                    {Object.entries(BALANCE_ANCHOR_LABEL).map(([code, label]) => (
                      <option key={code} value={code}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex w-24 flex-col gap-1 text-sm">
                  <span className="cell-nowrap text-gray-600">잔금 일수</span>
                  <input
                    inputMode="numeric"
                    value={f.balance_days}
                    onChange={(e) => set("balance_days")(e.target.value)}
                    className={`${inputClass} text-center`}
                  />
                </label>
              </div>
            </>
          )}
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">인코텀즈</span>
            <select value={f.incoterm_code} onChange={(e) => set("incoterm_code")(e.target.value)} className={inputClass}>
              <option value="">선택 안 함</option>
              {INCOTERM_CODES.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          </label>
          {f.incoterm_code !== "" && (
            <>
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-gray-600">인도 장소</span>
                <input
                  value={f.incoterm_place}
                  onChange={(e) => set("incoterm_place")(e.target.value)}
                  className={inputClass}
                />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-gray-600">인코텀즈 판(연도)</span>
                <select
                  value={f.incoterm_year}
                  onChange={(e) => set("incoterm_year")(e.target.value)}
                  className={inputClass}
                >
                  <option value="2020">2020</option>
                  <option value="2010">2010</option>
                </select>
              </label>
            </>
          )}
        </div>
      </fieldset>
      {(localError || save.error) && (
        <div role="alert" className="mt-3 text-sm text-signal-red">
          <p className="break-keep">
            {localError ??
              (occupant !== null && save.error instanceof ApiError
                ? save.error.message
                : soErrorMessage(save.error, "수주를 저장하지 못했습니다.", NOUN))}
          </p>
          {occupant !== null && !localError && <p className="mt-1 break-keep font-medium">{occupant.text}</p>}
          {conflict && <p className="mt-1 break-keep">위의 '최신 내용 불러오기'로 화면을 새로 고쳐 주세요.</p>}
        </div>
      )}
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={save.isPending || !changed || docDateMissing || nameMissing || termsProblem !== null}
          className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
        >
          {save.isPending ? "저장 중…" : "헤더 저장"}
        </button>
        {!changed && !save.isPending && <span className="text-sm text-gray-500">바뀐 내용이 없습니다.</span>}
        {docDateMissing && (
          <span role="alert" className="text-sm text-signal-red">
            증빙일은 필수입니다.
          </span>
        )}
        {termsProblem !== null && (
          <span role="alert" className="text-sm text-signal-red">
            {termsProblem}
          </span>
        )}
        {nameMissing && (
          <span role="alert" className="text-sm text-signal-red">
            서류상 바이어명은 비울 수 없습니다.
          </span>
        )}
        {save.isSuccess && !changed && (
          <span role="status" className="text-sm text-gray-500">
            저장했습니다.
          </span>
        )}
      </div>
    </form>
  );
}

// ── FREE 열(내부 메모·담당자) — 동결 후에도 편집 ────────────────────────────

function MetaPanel({
  so,
  onSaved,
  onError,
}: {
  so: SalesOrderDetail;
  onSaved: (next: SalesOrderDetail) => void;
  onError: (error: unknown) => void;
}) {
  const users = usePagedQuery<UserLookup>(["users", "lookup"], "/v1/users/lookup?size=200");
  const [note, setNote] = useState(so.internal_note ?? "");
  const [assignee, setAssignee] = useState(String(so.assignee_id));
  const lock = useRef(false);

  const save = useMutation({
    mutationFn: () =>
      apiFetch<SalesOrderDetail>(`/v1/sales-orders/${so.id}/meta`, {
        method: "PATCH",
        body: { version: so.version, internal_note: orNull(note), assignee_id: Number(assignee) },
      }),
    onSuccess: onSaved,
    onError,
  });

  const items = users.data?.items ?? [];
  const known = items.some((user) => String(user.id) === assignee);
  // 바뀐 게 없으면 저장하지 않는다 — 불필요한 version 증가가 다른 화면의 409를 부른다.
  const changed = note.trim() !== (so.internal_note ?? "") || assignee !== String(so.assignee_id);

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
        바이어에게 나가지 않는 항목입니다. 동결·보류된 뒤에도 고칠 수 있습니다.
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

// ── 라인 ──────────────────────────────────────────────────────────────────

const SKU_STATUS_LABEL: Record<string, string> = { DISCONTINUED: "단종", DELETED: "삭제됨" };

function LinesSection({
  so,
  editable,
  detailKey,
  onError,
  onChanged,
}: {
  so: SalesOrderDetail;
  editable: boolean;
  detailKey: readonly unknown[];
  onError: (error: unknown) => void;
  onChanged: (version: number) => void;
}) {
  const client = useQueryClient();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [removeTarget, setRemoveTarget] = useState<SalesOrderLine | null>(null);
  const removeLock = useRef(false);

  // 편집 불가(보류·취소 등)로 바뀌면 열려 있던 라인 수정 폼·제외 다이얼로그를 닫는다 —
  // 동결된 수주에 옛 폼으로 쓰기를 보내는 일을 화면에서 먼저 없앤다(서버는 어차피 409로 막는다).
  useEffect(() => {
    if (!editable) {
      setEditingId(null);
      setRemoveTarget(null);
    }
  }, [editable]);

  /** 라인 변경 응답의 헤더 version·합계를 즉시 반영한다 — 연속 편집이 자기 자신에게 409를 내지 않게. */
  function applyMutation(result: SoLineMutation) {
    client.setQueryData<SalesOrderDetail>(detailKey, (previous) =>
      previous
        ? { ...previous, version: result.header_version, total_amount: result.total_amount, total_text: result.total_text }
        : previous,
    );
    void client.invalidateQueries({ queryKey: detailKey, exact: true });
    onChanged(result.header_version);
  }

  const remove = useMutation({
    mutationFn: (line: SalesOrderLine) =>
      apiFetch<SoLineMutation>(`/v1/sales-orders/${so.id}/lines/${line.id}?version=${so.version}`, { method: "DELETE" }),
    onSuccess: (result) => {
      setRemoveTarget(null);
      applyMutation(result);
    },
    onError: (error) => {
      setRemoveTarget(null);
      onError(error);
    },
  });

  const cols = editable ? 10 : 9;

  return (
    <section aria-labelledby="so-lines-title">
      <h2 id="so-lines-title" className="text-lg font-semibold">
        라인
      </h2>
      <div className="mt-2 overflow-x-auto rounded-lg border border-gray-200">
        {so.lines.length === 0 ? (
          <p className="p-5 text-gray-500">
            {editable ? "라인이 없습니다. 아래에서 SKU를 추가하세요." : "라인이 없습니다."}
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="cell-nowrap px-3 py-2 text-center">번호</th>
                <th className="cell-nowrap px-3 py-2">SKU</th>
                <th className="cell-nowrap px-3 py-2">품명</th>
                <th className="cell-nowrap px-3 py-2 text-center">수량</th>
                <th className="cell-nowrap px-3 py-2 text-center">요청납기</th>
                <th className="cell-nowrap px-3 py-2 text-center">단가</th>
                <th className="cell-nowrap px-3 py-2 text-center">기준</th>
                <th className="cell-nowrap px-3 py-2 text-center">금액</th>
                <th className="cell-nowrap px-3 py-2">원천 대비</th>
                {editable && <th className="cell-nowrap px-3 py-2 text-center">편집</th>}
              </tr>
            </thead>
            <tbody>
              {so.lines.map((line) =>
                editable && editingId === line.id ? (
                  <LineEditRow
                    key={line.id}
                    so={so}
                    line={line}
                    colSpan={cols}
                    onDone={(result) => {
                      setEditingId(null);
                      applyMutation(result);
                    }}
                    onCancel={() => setEditingId(null)}
                    onError={onError}
                  />
                ) : (
                  <tr key={line.id} className="border-t border-gray-100 align-top">
                    <td className="num cell-nowrap px-3 py-2">{line.line_no}</td>
                    <td className="cell-nowrap px-3 py-2">{line.sku_code}</td>
                    <td className="break-keep px-3 py-2">
                      {line.sku_name_ko}
                      {line.sku_status !== null && line.sku_status !== "ACTIVE" && (
                        <span className="ml-2 rounded border border-signal-red px-1.5 py-0.5 text-xs text-signal-red">
                          {SKU_STATUS_LABEL[line.sku_status] ?? line.sku_status}
                        </span>
                      )}
                      {line.buyer_item_code && (
                        <span className="block text-xs text-gray-500">바이어 품번 {line.buyer_item_code}</span>
                      )}
                      {line.price_reason && <span className="block text-xs text-gray-500">사유: {line.price_reason}</span>}
                    </td>
                    <td className="num cell-nowrap px-3 py-2">{line.quantity}</td>
                    <td className="num cell-nowrap px-3 py-2">{line.requested_delivery_date ?? "—"}</td>
                    <td className="num cell-nowrap px-3 py-2">{line.is_free ? "무상" : line.unit_price_text}</td>
                    <td className="cell-nowrap px-3 py-2 text-center">
                      {PRICE_BASIS_LABEL[line.price_basis] ?? line.price_basis}
                    </td>
                    <td className="num cell-nowrap px-3 py-2">{line.line_amount_text}</td>
                    <td className="break-keep px-3 py-2 text-xs text-gray-600">
                      {line.source === null ? (
                        "—"
                      ) : (
                        <>
                          <span className="block">
                            원천 수량 {line.source.quantity}
                            {line.quantity_delta !== null && line.quantity_delta !== 0 && (
                              <> ({line.quantity_delta > 0 ? "+" : ""}
                              {line.quantity_delta})</>
                            )}
                          </span>
                          <span className="block">
                            원천 단가 {line.source.unit_price_text}
                            {line.price_changed === true && " — 변경됨"}
                          </span>
                        </>
                      )}
                    </td>
                    {editable && (
                      <td className="cell-nowrap px-3 py-2 text-center">
                        <button type="button" onClick={() => setEditingId(line.id)} className="underline">
                          수정
                        </button>
                        <button
                          type="button"
                          onClick={() => setRemoveTarget(line)}
                          className="ml-3 text-gray-500 underline"
                        >
                          제외
                        </button>
                      </td>
                    )}
                  </tr>
                ),
              )}
            </tbody>
            <tfoot>
              <tr className="border-t border-gray-200 bg-gray-50 font-semibold">
                <td colSpan={7} className="cell-nowrap px-3 py-2 text-right">
                  합계 (서버 계산)
                </td>
                <td className="num cell-nowrap px-3 py-2">
                  {so.total_text} {so.currency}
                </td>
                <td colSpan={editable ? 2 : 1} />
              </tr>
            </tfoot>
          </table>
        )}
      </div>

      {editable && <LineAddForm so={so} onDone={applyMutation} onError={onError} />}

      {editable && removeTarget && (
        <ConfirmDialog
          title="라인을 제외할까요?"
          danger
          confirmLabel="제외"
          description={
            <p>
              {removeTarget.sku_code} {removeTarget.sku_name_ko} 라인을 제외합니다. 라인 번호는 다시 쓰이지 않고
              {so.is_reference ? " 원천 수량은 잔량으로 돌아갑니다(같은 SKU는 다시 추가할 수 있습니다)." : " 같은 SKU는 다시 추가할 수 있습니다."}
            </p>
          }
          pending={remove.isPending}
          onCancel={() => setRemoveTarget(null)}
          onConfirm={() => {
            if (removeLock.current) return;
            removeLock.current = true;
            remove.mutate(removeTarget, { onSettled: () => (removeLock.current = false) });
          }}
        />
      )}
    </section>
  );
}

const isPositiveInt = (value: string): boolean => /^[1-9][0-9]*$/.test(value.trim()) && Number(value) <= MAX_QUANTITY;

function LineAddForm({
  so,
  onDone,
  onError,
}: {
  so: SalesOrderDetail;
  onDone: (result: SoLineMutation) => void;
  onError: (error: unknown) => void;
}) {
  const [sku, setSku] = useState<Sku | null>(null);
  const [quantity, setQuantity] = useState("1");
  const [unitPrice, setUnitPrice] = useState("");
  const [isFree, setIsFree] = useState(false);
  const [reason, setReason] = useState("");
  const [buyerCode, setBuyerCode] = useState("");
  const [delivery, setDelivery] = useState("");
  const [localError, setLocalError] = useState<unknown>(null);
  const lock = useRef(false);
  const reference = so.is_reference;

  const add = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {
        version: so.version,
        sku_id: sku?.id,
        quantity: Number(quantity),
      };
      // 참조 수주는 원천 라인 값이 복사된다 — 가격 필드를 보내지 않는다. 직접 수주는 단가를 비우면 마스터 판가.
      if (!reference) {
        body.is_free = isFree;
        if (!isFree && orNull(unitPrice) !== null) body.unit_price = orNull(unitPrice);
        if (orNull(reason) !== null) body.price_reason = orNull(reason);
      }
      if (orNull(buyerCode) !== null) body.buyer_item_code = orNull(buyerCode);
      if (delivery !== "") body.requested_delivery_date = delivery;
      return apiFetch<SoLineMutation>(`/v1/sales-orders/${so.id}/lines`, { method: "POST", body });
    },
    onSuccess: (result) => {
      setSku(null);
      setQuantity("1");
      setUnitPrice("");
      setIsFree(false);
      setReason("");
      setBuyerCode("");
      setDelivery("");
      setLocalError(null);
      onDone(result);
    },
    // 오류는 한 곳에만 — 낙관 잠금 충돌은 페이지 배너(최신 불러오기), 그 밖은 폼 안 경고.
    onError: (error) => {
      if (isVersionConflict(error)) onError(error);
      else setLocalError(error);
    },
  });

  const ready = sku !== null && isPositiveInt(quantity);

  return (
    <form
      aria-label="라인 추가"
      className="mt-3 rounded-lg border border-gray-200 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!ready || lock.current) return;
        lock.current = true;
        add.mutate(undefined, { onSettled: () => (lock.current = false) });
      }}
    >
      <h3 className="font-medium">라인 추가</h3>
      {reference && (
        <p className="mt-1 break-keep text-xs text-gray-500">
          원천에서 만든 수주는 원천에 있는 SKU만, 원천 남은 수량 안에서 추가할 수 있습니다(가격은 원천 값이 복사됩니다).
        </p>
      )}
      <fieldset disabled={add.isPending} className="contents">
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="flex flex-col gap-1 text-sm sm:col-span-2">
            <span className="text-gray-600">SKU</span>
            <SearchSelect<Sku>
              label="SKU"
              path="/v1/skus"
              queryKey={["skus"]}
              value={sku}
              onChange={setSku}
              getKey={(item) => item.id}
              getLabel={(item) => `${item.sku_code} ${item.name_ko}`}
            />
          </div>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">수량</span>
            <input
              inputMode="numeric"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              className={`${inputClass} text-center`}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">요청납기</span>
            <input type="date" value={delivery} onChange={(e) => setDelivery(e.target.value)} className={inputClass} />
          </label>
          {!reference && (
            <>
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-gray-600">단가 (비우면 마스터 판가)</span>
                <input
                  inputMode="decimal"
                  value={unitPrice}
                  disabled={isFree}
                  onChange={(e) => setUnitPrice(e.target.value)}
                  className={`${inputClass} text-center disabled:bg-gray-100`}
                />
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={isFree} onChange={(e) => setIsFree(e.target.checked)} />
                <span>무상 라인</span>
              </label>
              <label className="flex flex-col gap-1 text-sm sm:col-span-2">
                <span className="text-gray-600">가격·무상 사유 (선택, 무상은 필수)</span>
                <input value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} className={inputClass} />
              </label>
            </>
          )}
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">바이어 품번</span>
            <input value={buyerCode} maxLength={100} onChange={(e) => setBuyerCode(e.target.value)} className={inputClass} />
          </label>
        </div>
      </fieldset>
      {localError !== null && !isVersionConflict(localError) && (
        <p role="alert" className="mt-3 break-keep text-sm text-signal-red">
          {soErrorMessage(localError, undefined, NOUN)}
        </p>
      )}
      {quantity.trim() !== "" && !isPositiveInt(quantity) && (
        <p role="alert" className="mt-3 text-sm text-signal-red">
          수량은 1 이상의 정수로 입력해 주세요.
        </p>
      )}
      <button
        type="submit"
        disabled={!ready || add.isPending}
        className="cell-nowrap mt-3 rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
      >
        {add.isPending ? "추가 중…" : "라인 추가"}
      </button>
    </form>
  );
}

function LineEditRow({
  so,
  line,
  colSpan,
  onDone,
  onCancel,
  onError,
}: {
  so: SalesOrderDetail;
  line: SalesOrderLine;
  colSpan: number;
  onDone: (result: SoLineMutation) => void;
  onCancel: () => void;
  onError: (error: unknown) => void;
}) {
  const [quantity, setQuantity] = useState(String(line.quantity));
  const [unitPrice, setUnitPrice] = useState(line.unit_price_text);
  const [isFree, setIsFree] = useState(line.is_free);
  const [reason, setReason] = useState(line.price_reason ?? "");
  const [buyerCode, setBuyerCode] = useState(line.buyer_item_code ?? "");
  const [delivery, setDelivery] = useState(line.requested_delivery_date ?? "");
  const [localError, setLocalError] = useState<unknown>(null);
  const lock = useRef(false);

  /** 바뀐 필드만 — 단가를 바꿔야만 보낸다(보내면 기준이 수동[MANUAL]으로 바뀐다). 비어 있으면 저장할 것이 없다. */
  function changes(): Record<string, unknown> {
    const body: Record<string, unknown> = {};
    if (quantity.trim() !== String(line.quantity)) body.quantity = Number(quantity);
    if (!isFree && unitPrice.trim() !== line.unit_price_text) body.unit_price = unitPrice.trim();
    if (isFree !== line.is_free) body.is_free = isFree;
    if (reason !== (line.price_reason ?? "")) body.price_reason = reason;
    if (buyerCode.trim() !== (line.buyer_item_code ?? "")) body.buyer_item_code = buyerCode.trim();
    if (delivery !== (line.requested_delivery_date ?? "")) body.requested_delivery_date = delivery === "" ? null : delivery;
    return body;
  }
  const diff = changes();
  const changed = Object.keys(diff).length > 0;
  const quantityOk = isPositiveInt(quantity);
  // 원천 잔량 초과 409 — detail.open_quantity {원천 라인 id: 잔량}에서 이 라인의 몫을 보여 준다.
  const openHint = (() => {
    if (!(localError instanceof ApiError) || localError.code !== QUANTITY_EXCEEDS_OPEN_CODE) return null;
    const map = localError.detail.open_quantity;
    if (typeof map !== "object" || map === null || line.source === null) return null;
    const open = (map as Record<string, unknown>)[String(line.source.line_id)];
    return open === undefined ? null : String(open);
  })();

  const update = useMutation({
    mutationFn: () =>
      apiFetch<SoLineMutation>(`/v1/sales-orders/${so.id}/lines/${line.id}`, {
        method: "PATCH",
        body: { version: so.version, ...diff },
      }),
    onSuccess: onDone,
    // 오류는 한 곳에만 — 낙관 잠금 충돌은 페이지 배너(최신 불러오기), 그 밖은 폼 안 경고.
    onError: (error) => {
      if (isVersionConflict(error)) onError(error);
      else setLocalError(error);
    },
  });

  return (
    <tr className="border-t border-gray-100 bg-gray-50 align-top">
      <td colSpan={colSpan} className="px-3 py-3">
        <form
          aria-label={`라인 ${line.line_no} 수정`}
          className="flex flex-wrap items-end gap-3 text-sm"
          onSubmit={(event) => {
            event.preventDefault();
            if (lock.current || !changed || !quantityOk) return;
            lock.current = true;
            update.mutate(undefined, { onSettled: () => (lock.current = false) });
          }}
        >
          <span className="cell-nowrap font-medium">
            #{line.line_no} {line.sku_code}
          </span>
          <fieldset disabled={update.isPending} className="contents">
            <label className="flex flex-col gap-1">
              <span className="text-gray-600">수량</span>
              <input
                inputMode="numeric"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                className={`${inputClass} w-24 text-center`}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-gray-600">단가</span>
              <input
                inputMode="decimal"
                value={unitPrice}
                disabled={isFree}
                onChange={(e) => setUnitPrice(e.target.value)}
                className={`${inputClass} w-32 text-center disabled:bg-gray-100`}
              />
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={isFree} onChange={(e) => setIsFree(e.target.checked)} />
              <span>무상</span>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-gray-600">사유</span>
              <input value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} className={inputClass} />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-gray-600">바이어 품번</span>
              <input value={buyerCode} maxLength={100} onChange={(e) => setBuyerCode(e.target.value)} className={inputClass} />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-gray-600">요청납기</span>
              <input type="date" value={delivery} onChange={(e) => setDelivery(e.target.value)} className={inputClass} />
            </label>
          </fieldset>
          <button
            type="submit"
            disabled={update.isPending || !changed || !quantityOk}
            className="cell-nowrap rounded bg-gray-900 px-3 py-2 text-white disabled:opacity-50"
          >
            {update.isPending ? "저장 중…" : "저장"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={update.isPending}
            className="cell-nowrap rounded border border-gray-300 px-3 py-2 disabled:opacity-50"
          >
            취소
          </button>
        </form>
        {!quantityOk && (
          <p role="alert" className="mt-2 text-sm text-signal-red">
            수량은 1 이상의 정수로 입력해 주세요.
          </p>
        )}
        {!changed && quantityOk && <p className="mt-2 text-xs text-gray-500">바뀐 내용이 없습니다.</p>}
        {localError !== null && !isVersionConflict(localError) && (
          <p role="alert" className="mt-2 break-keep text-sm text-signal-red">
            {soErrorMessage(localError, undefined, NOUN)}
            {openHint !== null && ` — 원천 남은 수량 ${openHint}`}
          </p>
        )}
      </td>
    </tr>
  );
}
