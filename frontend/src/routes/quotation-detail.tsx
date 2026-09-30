// 견적 상세·편집 (S3-1 PR-5b — design-A QT 화면 / design-B B8 전이 UI / G-01·G-03).
//
// 규칙(서버가 정본, 화면은 편의 — §18.1):
// - 초안(DRAFT)만 CONTENT(가격·조건·라인) 편집. 발행 이후(frozen_at 있음)는 읽기 전용, FREE(내부 메모·담당자)만 /meta로 편집.
// - 모든 쓰기는 화면이 본 헤더 version을 싣는다(낙관 잠금). 409는 "다른 곳에서 먼저 수정" 안내와 '최신 내용 불러오기'.
// - 합계·라인 금액은 서버 문자열(*_text)만 그대로 표시한다. 프런트 산술 0.
// - 발행·취소·개정은 확인 다이얼로그(위험 동작 명시) + 멱등 키(다이얼로그를 여는 순간 1개 — 더블클릭·재시도 흡수).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ConfirmDialog } from "../components/confirm-dialog";
import { SearchSelect } from "../components/search-select";
import { StatusTimeline } from "../components/status-timeline";
import { ApiError, apiFetch } from "../lib/api";
import { errorMessage, isVersionConflict } from "../lib/api-errors";
import {
  BALANCE_ANCHOR_LABEL,
  INCOTERM_CODES,
  PAYMENT_TYPE_LABEL,
  PRICE_BASIS_LABEL,
  canCancelQuotation,
  canCreateProformaFrom,
  canEditQuotation,
  canIssueQuotation,
  canReviseQuotation,
  quotationStatusLabel,
} from "../lib/doc-status";
import { useCurrencies } from "../lib/money";
import { usePagedQuery } from "../lib/paging";
import {
  quotationDetailKey,
  QUOTATIONS_QUERY_KEY,
  type LineMutation,
  type QuotationDetail,
  type QuotationLine,
} from "../lib/quotation";
import { hasRole, useSession } from "../lib/session";
import { ProformaCreateDialog } from "./proforma-create";
import { QuotationStatusBadge } from "./quotations";
import type { Market } from "./markets";
import type { Partner } from "./partners";
import type { Sku } from "./skus";

type Action = "issue" | "cancel" | "revise";

interface UserLookup {
  id: number;
  display_name: string;
}

const EMPTY = "—";
const show = (value: string | number | null | undefined) =>
  value === null || value === undefined || value === "" ? EMPTY : String(value);

/** 빈 문자열 → null (서버는 null을 "값 지우기"로 읽는다). */
const orNull = (value: string): string | null => (value.trim() === "" ? null : value.trim());

/** 견적이 바뀌면(개정본 이동 등) 화면 상태 전체를 새로 시작한다 — 이전 견적의 기준 version·폼이 남지 않게. */
export function QuotationDetailPage() {
  const params = useParams();
  return <QuotationDetailView key={params.quotationId} />;
}

function QuotationDetailView() {
  const params = useParams();
  const id = Number(params.quotationId);
  const { me } = useSession();
  const canWrite = hasRole(me, "TRADE");
  const client = useQueryClient();
  const navigate = useNavigate();

  const detailKey = quotationDetailKey(id);
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: () => apiFetch<QuotationDetail>(`/v1/quotations/${id}`),
    enabled: Number.isInteger(id) && id > 0,
    // 편집 화면은 항상 서버의 최신 version을 기준으로 한다.
    staleTime: 0,
  });

  const [action, setAction] = useState<Action | null>(null);
  const [notice, setNotice] = useState<unknown>(null);
  const [resetToken, setResetToken] = useState(0);
  // ★ 화면이 "마지막으로 본/내가 쓴" version. 창 포커스 재조회로 서버 version이 앞서가도 쓰기는 이 값으로 보낸다 —
  //   폼은 옛 값인데 새 version으로 저장돼 다른 사람의 수정을 덮어쓰는 일을 서버 409가 막게 한다.
  //   null=아직 기준 없음(첫 조회 값 사용). 내 쓰기·'최신 내용 불러오기'에서만 갱신한다.
  const [baseVersion, setBaseVersion] = useState<number | null>(null);
  const [headerDirty, setHeaderDirty] = useState(false);
  const [creatingPi, setCreatingPi] = useState(false);
  const submitLock = useRef(false);
  const actionKey = useRef<string>("");

  /** 동기 잠금 — mutation의 isPending은 한 틱 늦게 켜져 같은 틱의 두 번째 클릭이 새어 나간다. */
  function submit(input: { kind: Action; version: number; reason: string }) {
    if (submitLock.current) return;
    submitLock.current = true;
    transition.mutate(input, { onSettled: () => (submitLock.current = false) });
  }

  function openAction(next: Action) {
    actionKey.current = crypto.randomUUID();
    setAction(next);
  }

  function reload() {
    setNotice(null);
    transition.reset();
    setAction(null);
    setCreatingPi(false);
    // 재조회가 끝난 뒤에 기준 version·폼을 새로 시드한다(옛 캐시로 시드하면 곧바로 또 어긋난다).
    void detail.refetch().then((result) => {
      if (result.data) setBaseVersion(result.data.version);
      setResetToken((value) => value + 1);
    });
  }

  function afterWrite(next: QuotationDetail) {
    client.setQueryData(detailKey, next);
    setBaseVersion(next.version);
    void client.invalidateQueries({ queryKey: QUOTATIONS_QUERY_KEY });
    setNotice(null);
  }

  const transition = useMutation({
    mutationFn: (input: { kind: Action; version: number; reason: string }) => {
      const options = { method: "POST", idempotencyKey: actionKey.current } as const;
      if (input.kind === "issue") {
        return apiFetch<QuotationDetail>(`/v1/quotations/${id}/issue`, {
          ...options,
          body: { version: input.version },
        });
      }
      if (input.kind === "cancel") {
        return apiFetch<QuotationDetail>(`/v1/quotations/${id}/transitions`, {
          ...options,
          body: { to: "CANCELLED", version: input.version, reason: input.reason },
        });
      }
      return apiFetch<QuotationDetail>(`/v1/quotations/${id}/revisions`, {
        ...options,
        body: { version: input.version },
      });
    },
    onSuccess: (result, input) => {
      setAction(null);
      void client.invalidateQueries({ queryKey: QUOTATIONS_QUERY_KEY });
      if (input.kind === "revise") {
        // 개정본은 새 초안이다 — 그 화면으로 보낸다(원본은 개정본 발행 때 취소된다).
        void navigate(`/quotations/${result.id}`);
      } else {
        afterWrite(result);
        setResetToken((value) => value + 1);
      }
    },
  });

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
          {notFound
            ? "견적을 찾을 수 없습니다."
            : errorMessage(detail.error, "견적을 불러오지 못했습니다.")}
        </p>
        <Link to="/quotations" className="mt-3 inline-block text-sm underline">
          견적 목록으로
        </Link>
      </section>
    );
  }

  const qt = detail.data;
  const base = baseVersion ?? qt.version;
  const stale = qt.version !== base;
  const writeQt = { ...qt, version: base }; // 쓰기용 — 폼이 시드된 기준 version
  const frozen = qt.frozen_at !== null;
  const editable = canWrite && canEditQuotation(qt.status) && !frozen;

  return (
    <section>
      <Link to="/quotations" className="cell-nowrap text-sm text-gray-500 underline">
        ← 견적 목록
      </Link>
      <header className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex flex-wrap items-center gap-3 text-2xl font-bold">
            <span className="cell-nowrap">{qt.doc_number}</span>
            <QuotationStatusBadge status={qt.status} lapsed={qt.is_lapsed} />
          </h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            {frozen
              ? "발행된 견적입니다. 가격·조건은 동결되어 고칠 수 없습니다(내부 메모·담당자만 수정 가능). 바꾸려면 개정본을 만드세요."
              : "초안입니다. 발행 전까지 자유롭게 고칠 수 있고, 발행하면 가격·조건이 동결됩니다."}
          </p>
          {qt.copied_from_id !== null && (
            <p className="mt-1 text-sm text-gray-500">
              원본 견적:{" "}
              <Link to={`/quotations/${qt.copied_from_id}`} className="underline">
                #{qt.copied_from_id}
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
          {canWrite && canIssueQuotation(qt.status) && (
            <button
              type="button"
              onClick={() => openAction("issue")}
              disabled={headerDirty}
              title={headerDirty ? "저장하지 않은 헤더 변경이 있습니다. 먼저 '헤더 저장'을 누르세요." : undefined}
              className="cell-nowrap rounded bg-gray-900 px-3 py-2 text-sm text-white disabled:opacity-50"
            >
              발행
            </button>
          )}
          {canWrite && canCreateProformaFrom(qt.status) && (
            <button
              type="button"
              onClick={() => setCreatingPi(true)}
              disabled={stale || qt.is_lapsed}
              title={
                qt.is_lapsed
                  ? "유효기간이 지난 견적으로는 PI를 만들 수 없습니다."
                  : stale
                    ? "다른 곳에서 수정되었습니다. 먼저 '최신 내용 불러오기'를 누르세요."
                    : undefined
              }
              className="cell-nowrap rounded border border-gray-900 px-3 py-2 text-sm disabled:opacity-50"
            >
              PI 만들기
            </button>
          )}
          {canWrite && canReviseQuotation(qt.status) && (
            <button
              type="button"
              onClick={() => openAction("revise")}
              className="cell-nowrap rounded border border-gray-900 px-3 py-2 text-sm"
            >
              개정본 작성
            </button>
          )}
          {canWrite && canCancelQuotation(qt.status) && (
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

      {headerDirty && canIssueQuotation(qt.status) && (
        <p role="status" className="mt-3 break-keep text-sm text-gray-600">
          저장하지 않은 헤더 변경이 있습니다. 발행하려면 먼저 '헤더 저장'을 누르세요.
        </p>
      )}

      {notice !== null && (
        <div role="alert" className="mt-4 rounded border border-signal-red p-3 text-sm text-signal-red">
          <p className="break-keep">{errorMessage(notice)}</p>
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
            다른 곳에서 이 견적이 수정되었습니다. 아래 라인·합계는 최신이지만 편집 폼은 이전 내용입니다. 저장하면 충돌(409)로
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
          <HeaderEditor
            key={`${qt.id}-${resetToken}`}
            qt={writeQt}
            onSaved={afterWrite}
            onError={setNotice}
            onDirty={setHeaderDirty}
          />
        ) : (
          <HeaderReadOnly qt={qt} />
        )}

        {canWrite && (
          <MetaPanel key={`meta-${qt.id}-${resetToken}`} qt={writeQt} onSaved={afterWrite} onError={setNotice} />
        )}

        <LinesSection
          qt={writeQt}
          editable={editable}
          detailKey={detailKey}
          onError={setNotice}
          onChanged={(version) => {
            setBaseVersion(version);
            setNotice(null);
            void client.invalidateQueries({ queryKey: QUOTATIONS_QUERY_KEY });
          }}
        />

        <StatusTimeline
          basePath={`/v1/quotations/${qt.id}`}
          queryKey={detailKey}
          statusLabel={quotationStatusLabel}
        />
      </div>

      {creatingPi && canWrite && canCreateProformaFrom(qt.status) && (
        <ProformaCreateDialog
          qt={qt}
          version={base}
          onClose={() => setCreatingPi(false)}
          onReload={reload}
        />
      )}
      {action === "issue" && (
        <ConfirmDialog
          title="견적을 발행할까요?"
          danger
          confirmLabel="발행"
          description={
            <>
              <p>
                발행하면 <strong>가격·환율·결제조건·라인이 동결</strong>되어 다시 고칠 수 없습니다. 고치려면
                개정본을 만들어야 합니다.
              </p>
              <p className="mt-1">
                {qt.doc_number} · 합계 {qt.total_text} {qt.currency}
              </p>
            </>
          }
          pending={transition.isPending}
          error={transition.error ? errorMessage(transition.error) : null}
          onReload={isVersionConflict(transition.error) ? reload : undefined}
          onCancel={() => {
            transition.reset();
            setAction(null);
          }}
          onConfirm={() => submit({ kind: "issue", version: base, reason: "" })}
        />
      )}
      {action === "cancel" && (
        <ConfirmDialog
          title="견적을 취소할까요?"
          danger
          confirmLabel="취소 확정"
          reasonLabel="취소 사유 (필수)"
          description={
            <>
              <p>
                취소하면 <strong>되돌릴 수 없습니다.</strong> 견적번호는 남고 다시 쓰이지 않습니다.
                {frozen && " 이 견적에서 이어진 문서가 살아 있으면 취소되지 않습니다."}
              </p>
            </>
          }
          pending={transition.isPending}
          error={transition.error ? errorMessage(transition.error) : null}
          onReload={isVersionConflict(transition.error) ? reload : undefined}
          onCancel={() => {
            transition.reset();
            setAction(null);
          }}
          onConfirm={(reason) => submit({ kind: "cancel", version: base, reason })}
        />
      )}
      {action === "revise" && (
        <ConfirmDialog
          title="개정본을 만들까요?"
          confirmLabel="개정본 작성"
          description={
            <p>
              이 견적의 값을 복사한 <strong>새 초안</strong>을 만듭니다. 원본은 그대로 유효하며,
              <strong> 개정본을 발행하는 순간 원본이 자동으로 취소</strong>됩니다.
            </p>
          }
          pending={transition.isPending}
          error={transition.error ? errorMessage(transition.error) : null}
          onReload={isVersionConflict(transition.error) ? reload : undefined}
          onCancel={() => {
            transition.reset();
            setAction(null);
          }}
          onConfirm={() => submit({ kind: "revise", version: base, reason: "" })}
        />
      )}
    </section>
  );
}

// ── 읽기 전용 헤더(동결·종결) ──────────────────────────────────────────────

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="cell-nowrap text-xs text-gray-500">{label}</dt>
      <dd className="mt-0.5 break-keep text-sm">{children}</dd>
    </div>
  );
}

function termsText(qt: QuotationDetail): string {
  const t = qt.payment_terms;
  if (t.payment_type === null) return EMPTY;
  const parts: string[] = [PAYMENT_TYPE_LABEL[t.payment_type] ?? t.payment_type];
  if (t.advance_pct !== null) parts.push(`선수금 ${t.advance_pct}%`);
  if (t.balance_anchor !== null) {
    parts.push(
      `잔금 ${BALANCE_ANCHOR_LABEL[t.balance_anchor] ?? t.balance_anchor} 기준 ${show(t.balance_days)}일`,
    );
  }
  return parts.join(" · ");
}

function HeaderReadOnly({ qt }: { qt: QuotationDetail }) {
  return (
    <section aria-label="견적 헤더" className="rounded-lg border border-gray-200 p-4">
      <h2 className="text-lg font-semibold">견적 정보</h2>
      <dl className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="바이어">{qt.buyer_name}</Field>
        <Field label="목적지">{qt.dest_market_code}</Field>
        <Field label="통화">{qt.currency}</Field>
        <Field label="증빙일">{qt.doc_date}</Field>
        <Field label="유효기간">{show(qt.valid_until)}</Field>
        <Field label="환율">
          {qt.fx_rate === null ? EMPTY : `${qt.fx_rate} (기준일 ${show(qt.fx_rate_date)})`}
          {qt.fx_rate_age_days !== null && qt.fx_rate_age_days > 0 && (
            <span className="ml-2 text-gray-500">{qt.fx_rate_age_days}일 지남</span>
          )}
        </Field>
        <Field label="결제조건">{termsText(qt)}</Field>
        <Field label="인코텀즈">
          {qt.incoterm.code === null
            ? EMPTY
            : `${qt.incoterm.code} ${show(qt.incoterm.place)} (${show(qt.incoterm.year)})`}
        </Field>
        <Field label="바이어 주소">{show(qt.buyer_address)}</Field>
      </dl>
    </section>
  );
}

// ── 초안 헤더 편집 ─────────────────────────────────────────────────────────

const inputClass = "rounded border border-gray-300 px-3 py-2 text-sm";

function HeaderEditor({
  qt,
  onSaved,
  onError,
  onDirty,
}: {
  qt: QuotationDetail;
  onSaved: (next: QuotationDetail) => void;
  onError: (error: unknown) => void;
  onDirty: (dirty: boolean) => void;
}) {
  const currencies = useCurrencies();
  const markets = usePagedQuery<Market>(["markets", "select"], "/v1/markets?size=200");
  // 바이어 선택값은 라벨 표시용으로 이름만 안다 — 변경했을 때만 id를 보낸다.
  const [buyer, setBuyer] = useState<Partner | null>(null);
  const [changeBuyer, setChangeBuyer] = useState(false);
  const [f, setF] = useState({
    dest_market_code: qt.dest_market_code,
    currency: qt.currency,
    doc_date: qt.doc_date,
    valid_until: qt.valid_until ?? "",
    fx_rate: qt.fx_rate ?? "",
    fx_rate_date: qt.fx_rate_date ?? "",
    payment_type: qt.payment_terms.payment_type ?? "",
    advance_pct: qt.payment_terms.advance_pct ?? "",
    balance_anchor: qt.payment_terms.balance_anchor ?? "",
    balance_days: qt.payment_terms.balance_days === null ? "" : String(qt.payment_terms.balance_days),
    incoterm_code: qt.incoterm.code ?? "",
    incoterm_place: qt.incoterm.place ?? "",
    incoterm_year: String(qt.incoterm.year ?? 2020),
    buyer_name: qt.buyer_name,
    buyer_address: qt.buyer_address ?? "",
  });
  const initial = useRef(JSON.stringify(f));
  const dirty = changeBuyer || JSON.stringify(f) !== initial.current;
  useEffect(() => {
    onDirty(dirty);
    return () => onDirty(false);
  }, [dirty, onDirty]);
  const set = (key: keyof typeof f) => (value: string) => setF((prev) => ({ ...prev, [key]: value }));

  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {
        version: qt.version,
        dest_market_code: f.dest_market_code,
        doc_date: f.doc_date,
        valid_until: orNull(f.valid_until),
        buyer_name: orNull(f.buyer_name),
        buyer_address: orNull(f.buyer_address),
        payment_terms:
          f.payment_type === ""
            ? null
            : {
                payment_type: f.payment_type,
                advance_pct: orNull(f.advance_pct),
                balance_anchor: orNull(f.balance_anchor),
                balance_days: f.balance_days === "" ? null : Number(f.balance_days),
              },
        incoterm:
          f.incoterm_code === ""
            ? null
            : { code: f.incoterm_code, place: f.incoterm_place.trim(), year: Number(f.incoterm_year) },
      };
      if (f.currency !== qt.currency) body.currency = f.currency;
      // KRW는 서버가 환율 1·증빙일을 채운다 — 화면이 값을 만들지 않는다.
      if (f.currency !== "KRW") {
        body.fx_rate = orNull(f.fx_rate);
        body.fx_rate_date = orNull(f.fx_rate_date);
      }
      if (changeBuyer && buyer !== null) body.buyer_partner_id = buyer.id;
      return apiFetch<QuotationDetail>(`/v1/quotations/${qt.id}`, { method: "PATCH", body });
    },
    onSuccess: (next) => {
      // 저장한 값이 새 기준이다 — 미저장(dirty) 표시를 끈다.
      initial.current = JSON.stringify(f);
      setChangeBuyer(false);
      setBuyer(null);
      onSaved(next);
    },
    onError,
  });

  // 통화는 라인이 한 번이라도 있었으면 바꿀 수 없다(서버 규칙 — 제외한 라인도 포함).
  const currencyLocked = qt.last_line_no > 0;
  const payType = f.payment_type;

  return (
    <form
      aria-label="견적 헤더 편집"
      className="rounded-lg border border-gray-200 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <h2 className="text-lg font-semibold">견적 정보 (초안 편집)</h2>
      <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">바이어</span>
          {changeBuyer ? (
            <SearchSelect<Partner>
              label="바이어 변경"
              path="/v1/partners"
              params={{ type: "BUYER" }}
              queryKey={["partners"]}
              value={buyer}
              onChange={(next) => {
                setBuyer(next);
                if (next === null) setChangeBuyer(false);
              }}
              getKey={(item) => item.id}
              getLabel={(item) => `${item.name_ko} (${item.partner_code})`}
            />
          ) : (
            <div className="flex items-center gap-2 rounded border border-gray-300 px-3 py-2">
              <span className="break-keep">{qt.buyer_name}</span>
              <button
                type="button"
                onClick={() => setChangeBuyer(true)}
                className="cell-nowrap text-gray-500 underline"
              >
                바이어 변경
              </button>
            </div>
          )}
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">목적지 시장</span>
          <select
            value={f.dest_market_code}
            onChange={(e) => set("dest_market_code")(e.target.value)}
            className={inputClass}
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
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">통화</span>
          <select
            value={f.currency}
            disabled={currencyLocked}
            onChange={(e) => set("currency")(e.target.value)}
            className={`${inputClass} disabled:bg-gray-100`}
          >
            {currencies.data?.items.map((item) => (
              <option key={item.code} value={item.code}>
                {item.code}
              </option>
            ))}
            {!currencies.data?.items.some((item) => item.code === f.currency) && (
              <option value={f.currency}>{f.currency}</option>
            )}
          </select>
          {currencyLocked && (
            <span className="break-keep text-xs text-gray-500">
              라인을 한 번이라도 넣은 견적은 통화를 바꿀 수 없습니다.
            </span>
          )}
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">증빙일</span>
          <input type="date" value={f.doc_date} onChange={(e) => set("doc_date")(e.target.value)} className={inputClass} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">유효기간 (발행 필수)</span>
          <input type="date" value={f.valid_until} onChange={(e) => set("valid_until")(e.target.value)} className={inputClass} />
        </label>
        <div className="hidden lg:block" />
        {f.currency === "KRW" ? (
          <p className="break-keep text-sm text-gray-500 sm:col-span-2 lg:col-span-3">
            원화(KRW) 견적은 환율 1로 서버가 채웁니다.
          </p>
        ) : (
          <>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-gray-600">환율 (발행 필수)</span>
              <input
                inputMode="decimal"
                value={f.fx_rate}
                onChange={(e) => set("fx_rate")(e.target.value)}
                className={`${inputClass} text-center`}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-gray-600">환율 기준일</span>
              <input type="date" value={f.fx_rate_date} onChange={(e) => set("fx_rate_date")(e.target.value)} className={inputClass} />
              {qt.fx_rate_age_days !== null && qt.fx_rate_age_days > 0 && (
                <span className="text-xs text-gray-500">저장된 기준일은 {qt.fx_rate_age_days}일 지났습니다.</span>
              )}
            </label>
            <div className="hidden lg:block" />
          </>
        )}
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">결제조건</span>
          <select value={payType} onChange={(e) => set("payment_type")(e.target.value)} className={inputClass}>
            <option value="">선택 안 함</option>
            {Object.entries(PAYMENT_TYPE_LABEL)
              .filter(([code]) => code !== "LC" || qt.payment_terms.payment_type === "LC")
              .map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
          </select>
        </label>
        {payType !== "" && (
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
                <select value={f.balance_anchor} onChange={(e) => set("balance_anchor")(e.target.value)} className={inputClass}>
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
              <input value={f.incoterm_place} onChange={(e) => set("incoterm_place")(e.target.value)} className={inputClass} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-gray-600">인코텀즈 판(연도)</span>
              <select value={f.incoterm_year} onChange={(e) => set("incoterm_year")(e.target.value)} className={inputClass}>
                <option value="2020">2020</option>
                <option value="2010">2010</option>
              </select>
            </label>
          </>
        )}
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">서류상 바이어명</span>
          <input value={f.buyer_name} maxLength={200} onChange={(e) => set("buyer_name")(e.target.value)} className={inputClass} />
        </label>
        <label className="flex flex-col gap-1 text-sm sm:col-span-2">
          <span className="text-gray-600">바이어 주소</span>
          <input value={f.buyer_address} maxLength={500} onChange={(e) => set("buyer_address")(e.target.value)} className={inputClass} />
        </label>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button
          type="submit"
          disabled={save.isPending || f.doc_date === ""}
          className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
        >
          {save.isPending ? "저장 중…" : "헤더 저장"}
        </button>
        {f.doc_date === "" && (
          <span role="alert" className="text-sm text-signal-red">
            증빙일은 필수입니다.
          </span>
        )}
        {save.isSuccess && <span role="status" className="text-sm text-gray-500">저장했습니다.</span>}
      </div>
    </form>
  );
}

// ── FREE 열(내부 메모·담당자) — 동결 후에도 편집 ───────────────────────────

function MetaPanel({
  qt,
  onSaved,
  onError,
}: {
  qt: QuotationDetail;
  onSaved: (next: QuotationDetail) => void;
  onError: (error: unknown) => void;
}) {
  const users = usePagedQuery<UserLookup>(["users", "lookup"], "/v1/users/lookup?size=200");
  const [note, setNote] = useState(qt.internal_note ?? "");
  const [assignee, setAssignee] = useState(String(qt.assignee_id));

  const save = useMutation({
    mutationFn: () =>
      apiFetch<QuotationDetail>(`/v1/quotations/${qt.id}/meta`, {
        method: "PATCH",
        body: { version: qt.version, internal_note: orNull(note), assignee_id: Number(assignee) },
      }),
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
        save.mutate();
      }}
    >
      <h2 className="text-lg font-semibold">내부 메모·담당자</h2>
      <p className="mt-1 break-keep text-sm text-gray-500">
        바이어에게 나가지 않는 항목입니다. 발행 후에도 고칠 수 있습니다.
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
        disabled={save.isPending}
        className="cell-nowrap mt-3 rounded border border-gray-300 px-4 py-2 text-sm disabled:opacity-50"
      >
        {save.isPending ? "저장 중…" : "메모·담당자 저장"}
      </button>
    </form>
  );
}

// ── 라인 ──────────────────────────────────────────────────────────────────

function LinesSection({
  qt,
  editable,
  detailKey,
  onError,
  onChanged,
}: {
  qt: QuotationDetail;
  editable: boolean;
  detailKey: readonly unknown[];
  onError: (error: unknown) => void;
  onChanged: (version: number) => void;
}) {
  const client = useQueryClient();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [removeTarget, setRemoveTarget] = useState<QuotationLine | null>(null);

  // 편집 불가(발행·취소 등)로 바뀌면 열려 있던 라인 수정 폼·제외 다이얼로그를 닫는다 —
  // 동결된 견적에 옛 폼으로 쓰기를 보내는 일을 화면에서 먼저 없앤다(서버는 어차피 409로 막는다).
  useEffect(() => {
    if (!editable) {
      setEditingId(null);
      setRemoveTarget(null);
    }
  }, [editable]);

  /** 라인 변경 응답의 헤더 version·합계를 즉시 반영한다 — 연속 편집이 자기 자신에게 409를 내지 않게. */
  function applyMutation(result: LineMutation) {
    client.setQueryData<QuotationDetail>(detailKey, (previous) =>
      previous
        ? {
            ...previous,
            version: result.header_version,
            total_amount: result.total_amount,
            total_text: result.total_text,
          }
        : previous,
    );
    void client.invalidateQueries({ queryKey: detailKey, exact: true });
    onChanged(result.header_version);
  }

  const remove = useMutation({
    mutationFn: (line: QuotationLine) =>
      apiFetch<LineMutation>(`/v1/quotations/${qt.id}/lines/${line.id}?version=${qt.version}`, {
        method: "DELETE",
      }),
    onSuccess: (result) => {
      setRemoveTarget(null);
      applyMutation(result);
    },
    onError: (error) => {
      setRemoveTarget(null);
      onError(error);
    },
  });

  return (
    <section aria-labelledby="qt-lines-title">
      <h2 id="qt-lines-title" className="text-lg font-semibold">
        라인
      </h2>
      <div className="mt-2 overflow-x-auto rounded-lg border border-gray-200">
        {qt.lines.length === 0 ? (
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
                <th className="cell-nowrap px-3 py-2 text-center">단가</th>
                <th className="cell-nowrap px-3 py-2 text-center">기준</th>
                <th className="cell-nowrap px-3 py-2 text-center">금액</th>
                {editable && <th className="cell-nowrap px-3 py-2 text-center">편집</th>}
              </tr>
            </thead>
            <tbody>
              {qt.lines.map((line) =>
                editable && editingId === line.id ? (
                  <LineEditRow
                    key={line.id}
                    qt={qt}
                    line={line}
                    colSpan={editable ? 8 : 7}
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
                      {line.buyer_item_code && (
                        <span className="block text-xs text-gray-500">바이어 품번 {line.buyer_item_code}</span>
                      )}
                      {line.price_reason && (
                        <span className="block text-xs text-gray-500">사유: {line.price_reason}</span>
                      )}
                    </td>
                    <td className="num cell-nowrap px-3 py-2">{line.quantity}</td>
                    <td className="num cell-nowrap px-3 py-2">
                      {line.is_free ? "무상" : line.unit_price_text}
                    </td>
                    <td className="cell-nowrap px-3 py-2 text-center">
                      {PRICE_BASIS_LABEL[line.price_basis] ?? line.price_basis}
                    </td>
                    <td className="num cell-nowrap px-3 py-2">{line.line_amount_text}</td>
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
                <td colSpan={6} className="cell-nowrap px-3 py-2 text-right">
                  합계 (서버 계산)
                </td>
                <td className="num cell-nowrap px-3 py-2">
                  {qt.total_text} {qt.currency}
                </td>
                {editable && <td />}
              </tr>
            </tfoot>
          </table>
        )}
      </div>

      {editable && <LineAddForm qt={qt} onDone={applyMutation} onError={onError} />}

      {editable && removeTarget && (
        <ConfirmDialog
          title="라인을 제외할까요?"
          danger
          confirmLabel="제외"
          description={
            <p>
              {removeTarget.sku_code} {removeTarget.sku_name_ko} 라인을 제외합니다. 라인 번호는 다시 쓰이지
              않습니다(같은 SKU는 다시 추가할 수 있습니다).
            </p>
          }
          pending={remove.isPending}
          onCancel={() => setRemoveTarget(null)}
          onConfirm={() => remove.mutate(removeTarget)}
        />
      )}
    </section>
  );
}

function LineAddForm({
  qt,
  onDone,
  onError,
}: {
  qt: QuotationDetail;
  onDone: (result: LineMutation) => void;
  onError: (error: unknown) => void;
}) {
  const [sku, setSku] = useState<Sku | null>(null);
  const [quantity, setQuantity] = useState("1");
  const [unitPrice, setUnitPrice] = useState("");
  const [isFree, setIsFree] = useState(false);
  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState<unknown>(null);

  const add = useMutation({
    mutationFn: () =>
      apiFetch<LineMutation>(`/v1/quotations/${qt.id}/lines`, {
        method: "POST",
        body: {
          version: qt.version,
          sku_id: sku?.id,
          quantity: Number(quantity),
          // 단가를 비우면 서버가 마스터 판가를 쓴다(기준=마스터). 입력하면 수동(MANUAL).
          unit_price: isFree ? undefined : orNull(unitPrice) ?? undefined,
          is_free: isFree,
          price_reason: orNull(reason) ?? undefined,
        },
      }),
    onSuccess: (result) => {
      setSku(null);
      setQuantity("1");
      setUnitPrice("");
      setIsFree(false);
      setReason("");
      setLocalError(null);
      onDone(result);
    },
    onError: (error) => {
      setLocalError(error);
      onError(error);
    },
  });

  const ready = sku !== null && /^[0-9]+$/.test(quantity) && Number(quantity) > 0;

  return (
    <form
      aria-label="라인 추가"
      className="mt-3 rounded-lg border border-gray-200 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) add.mutate();
      }}
    >
      <h3 className="font-medium">라인 추가</h3>
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
      </div>
      {localError !== null && !isVersionConflict(localError) && (
        <p role="alert" className="mt-3 break-keep text-sm text-signal-red">
          {errorMessage(localError)}
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
  qt,
  line,
  colSpan,
  onDone,
  onCancel,
  onError,
}: {
  qt: QuotationDetail;
  line: QuotationLine;
  colSpan: number;
  onDone: (result: LineMutation) => void;
  onCancel: () => void;
  onError: (error: unknown) => void;
}) {
  const [quantity, setQuantity] = useState(String(line.quantity));
  const [unitPrice, setUnitPrice] = useState(line.unit_price_text);
  const [isFree, setIsFree] = useState(line.is_free);
  const [reason, setReason] = useState(line.price_reason ?? "");
  const [localError, setLocalError] = useState<unknown>(null);

  const update = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { version: qt.version };
      if (Number(quantity) !== line.quantity) body.quantity = Number(quantity);
      // 단가를 바꿔야만 보낸다 — 보내면 기준이 수동(MANUAL)으로 바뀐다.
      if (!isFree && unitPrice.trim() !== line.unit_price_text) body.unit_price = unitPrice.trim();
      if (isFree !== line.is_free) body.is_free = isFree;
      if (reason !== (line.price_reason ?? "")) body.price_reason = reason;
      return apiFetch<LineMutation>(`/v1/quotations/${qt.id}/lines/${line.id}`, {
        method: "PATCH",
        body,
      });
    },
    onSuccess: onDone,
    onError: (error) => {
      setLocalError(error);
      onError(error);
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
            update.mutate();
          }}
        >
          <span className="cell-nowrap font-medium">
            #{line.line_no} {line.sku_code}
          </span>
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
          <button
            type="submit"
            disabled={update.isPending}
            className="cell-nowrap rounded bg-gray-900 px-3 py-2 text-white disabled:opacity-50"
          >
            {update.isPending ? "저장 중…" : "저장"}
          </button>
          <button type="button" onClick={onCancel} className="cell-nowrap rounded border border-gray-300 px-3 py-2">
            취소
          </button>
        </form>
        {localError !== null && !isVersionConflict(localError) && (
          <p role="alert" className="mt-2 break-keep text-sm text-signal-red">
            {errorMessage(localError)}
          </p>
        )}
      </td>
    </tr>
  );
}
