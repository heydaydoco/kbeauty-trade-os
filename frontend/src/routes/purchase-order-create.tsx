// 발주 만들기 — 2단: ① 입력 → ② 미리보기(저장 안 됨) → ③ '발주 확정'(=생성=발행) (S3-1 PR-8b — design-A A12 / design-F F2·F3 / ADR-0057).
//
// 규칙(서버가 정본, 화면은 편의 — §18.1):
// - PO는 초안이 없다 — 확정(생성) 요청이 곧 발행·동결이다. 되돌릴 수 없는 공급사 발송 대상이라 확인 다이얼로그에서 한 번 더 묻는다.
// - 미리보기(`/preview`)는 비저장(번호·이벤트·멱등 키 소비 없음) — 서버 계산값(단가·금액·합계)을 그대로 보여 준다. 프런트 산술 0.
// - 확정은 **미리보기 때의 본문 그대로** 보낸다(그사이 입력이 바뀌었어도 다른 것을 만들지 않는다). 증빙일을 비웠으면 미리보기가 준 날짜를 고정한다.
// - 멱등 키는 폼 인스턴스당 1개 — 새 키는 "마지막으로 서버에 보낸 본문과 실제로 다른 본문"을 보낼 때만(결과를 모르는 실패 뒤 재시도·더블클릭은 같은 키).
//   더블클릭은 동기 잠금(ref)으로 1회만 전송한다.
// - 공급사 유형 규칙(구매=공급사 또는 OEM, OEM 생산=OEM)은 서버가 판정하고 화면은 그 한국어 메시지를 그대로 보인다.
// - 쓰기 역할은 무역·관리자뿐이라 단가(원가) 입력란은 그 역할에게만 보인다. 자유 텍스트(내부 메모·공급사명)에는 원가를 적지 말라고 안내한다.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { ConfirmDialog } from "../components/confirm-dialog";
import { DocField, EMPTY, incotermText, paymentTermsText, show } from "../components/proforma-facts";
import { SearchSelect } from "../components/search-select";
import { apiFetch } from "../lib/api";
import { errorMessage } from "../lib/api-errors";
import {
  INCOTERM_CODES,
  PAYMENT_TYPE_LABEL,
  PO_BALANCE_ANCHOR_LABEL,
  PO_KIND_LABEL,
  PRICE_BASIS_LABEL,
} from "../lib/doc-status";
import { useCurrencies } from "../lib/money";
import { usePagedQuery } from "../lib/paging";
import {
  NO_COST_IN_FREE_TEXT,
  PO_RISK_NOTICE,
  PURCHASE_ORDERS_QUERY_KEY,
  purchaseOrderDetailKey,
  type PoCreateBody,
  type PoCreateLineBody,
  type PurchaseOrderDetail,
  type PurchaseOrderPreview,
} from "../lib/purchase-order";
import { hasRole, useSession } from "../lib/session";
import type { Partner } from "./partners";
import type { Sku } from "./skus";

const MAX_QUANTITY = 99_999_999; // 서버 MAX_QUANTITY와 같은 상한 — 형식 검증용
const MAX_LINES = 500; // 서버 요청 스키마 상한
const DECIMAL = /^\d+(\.\d+)?$/;
const inputClass = "rounded border border-gray-300 px-3 py-2 text-sm";

interface UserLookup {
  id: number;
  display_name: string;
}

interface LineForm {
  key: number;
  sku: Sku | null;
  qty: string;
  cost: string;
  delivery: string;
}

let lineKeySeq = 0;
const newLine = (): LineForm => ({ key: ++lineKeySeq, sku: null, qty: "", cost: "", delivery: "" });

export function PurchaseOrderCreatePage() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const { me } = useSession();
  const canWrite = hasRole(me, "TRADE");
  const currencies = useCurrencies();
  const users = usePagedQuery<UserLookup>(["users", "lookup"], "/v1/users/lookup?size=200", canWrite);

  const [supplier, setSupplier] = useState<Partner | null>(null);
  const [poKind, setPoKind] = useState("PURCHASE");
  const [currency, setCurrency] = useState("");
  const [docDate, setDocDate] = useState("");
  const [fxRate, setFxRate] = useState("");
  const [fxDate, setFxDate] = useState("");
  const [payType, setPayType] = useState("");
  const [advancePct, setAdvancePct] = useState("");
  const [anchor, setAnchor] = useState("");
  const [days, setDays] = useState("");
  const [incCode, setIncCode] = useState("");
  const [incPlace, setIncPlace] = useState("");
  const [incYear, setIncYear] = useState("2020");
  const [supplierName, setSupplierName] = useState("");
  const [note, setNote] = useState("");
  const [assignee, setAssignee] = useState("");
  const [lines, setLines] = useState<LineForm[]>(() => [newLine()]);

  const [formError, setFormError] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ data: PurchaseOrderPreview; body: PoCreateBody } | null>(null);
  const [confirming, setConfirming] = useState(false);

  const previewLock = useRef(false);
  const createLock = useRef(false);
  // 멱등 키는 폼 인스턴스당 1개. 새 키는 마지막으로 보낸 본문과 실제로 다른 본문을 보낼 때만.
  const keyRef = useRef<string>("");
  if (keyRef.current === "") keyRef.current = crypto.randomUUID();
  const lastSent = useRef<string | null>(null);

  const previewMutation = useMutation({
    mutationFn: (body: PoCreateBody) =>
      apiFetch<PurchaseOrderPreview>("/v1/purchase-orders/preview", { method: "POST", body }),
    // 증빙일을 비웠어도 미리보기가 준 날짜를 확정 본문에 고정한다 — 화면에서 본 날짜·마스터 단가 기준일과 발행이 같도록.
    onSuccess: (data, body) => setPreview({ data, body: { ...body, doc_date: body.doc_date ?? data.doc_date } }),
  });

  const createMutation = useMutation({
    mutationFn: (body: PoCreateBody) =>
      apiFetch<PurchaseOrderDetail>("/v1/purchase-orders", {
        method: "POST",
        idempotencyKey: keyRef.current,
        body,
      }),
    onSuccess: (created) => {
      client.setQueryData(purchaseOrderDetailKey(created.id), created);
      void client.invalidateQueries({ queryKey: PURCHASE_ORDERS_QUERY_KEY });
      void navigate(`/purchase-orders/${created.id}`);
    },
  });

  const busy = previewMutation.isPending || createMutation.isPending;

  /** 입력이 바뀌면 이전 시도의 오류를 버린다(키는 확정 시 본문 비교로 정한다). */
  function touched() {
    setFormError(null);
    previewMutation.reset();
    createMutation.reset();
  }

  function updateLine(key: number, patch: Partial<LineForm>) {
    touched();
    setLines((prev) => prev.map((line) => (line.key === key ? { ...line, ...patch } : line)));
  }

  function buildBody(): PoCreateBody | null {
    if (supplier === null) {
      setFormError("공급사를 선택해 주세요.");
      return null;
    }
    if (currency === "") {
      setFormError("통화를 선택해 주세요.");
      return null;
    }
    if (currency !== "KRW" && fxRate.trim() === "") {
      setFormError("환율을 입력해 주세요(원화가 아닌 발주는 환율이 필수입니다).");
      return null;
    }
    if (payType === "") {
      setFormError("결제조건을 선택해 주세요.");
      return null;
    }
    if (payType === "TT_ADVANCE" && advancePct.trim() === "") {
      setFormError("선수금 T/T는 선수금 비율을 입력해 주세요.");
      return null;
    }
    if (payType === "TT_DEFERRED" && (anchor === "" || days.trim() === "")) {
      setFormError("후불 T/T는 잔금 기준과 잔금 일수를 입력해 주세요.");
      return null;
    }
    if ((anchor === "") !== (days.trim() === "") && payType !== "LC") {
      setFormError("잔금 기준과 잔금 일수는 함께 입력해 주세요.");
      return null;
    }
    if (days.trim() !== "" && !/^-?\d+$/.test(days.trim())) {
      setFormError("잔금 일수는 정수로 입력해 주세요.");
      return null;
    }
    if (incCode === "" || incPlace.trim() === "") {
      setFormError("Incoterms 코드와 인도 장소를 입력해 주세요.");
      return null;
    }
    if (lines.length === 0) {
      setFormError("라인을 1개 이상 추가해 주세요.");
      return null;
    }
    const seen = new Set<number>();
    const bodyLines: PoCreateLineBody[] = [];
    for (const [index, line] of lines.entries()) {
      const label = `라인 ${index + 1}`;
      if (line.sku === null) {
        setFormError(`${label}의 SKU를 선택해 주세요.`);
        return null;
      }
      if (seen.has(line.sku.id)) {
        setFormError(`${label}: 같은 SKU(${line.sku.sku_code})가 이미 다른 라인에 있습니다. 수량을 합쳐 한 줄로 만들어 주세요.`);
        return null;
      }
      seen.add(line.sku.id);
      const qty = line.qty.trim();
      if (!/^[1-9][0-9]*$/.test(qty) || Number(qty) > MAX_QUANTITY) {
        setFormError(`${label}의 수량은 1 이상의 정수로 입력해 주세요.`);
        return null;
      }
      const cost = line.cost.trim();
      if (cost !== "" && !DECIMAL.test(cost)) {
        setFormError(`${label}의 단가는 숫자(예: 12.34)로 입력하거나 비워 두세요(비우면 마스터 매입가).`);
        return null;
      }
      if (line.delivery !== "" && docDate !== "" && line.delivery < docDate) {
        setFormError(`${label}의 요청납기는 증빙일 이후여야 합니다.`);
        return null;
      }
      const out: PoCreateLineBody = { sku_id: line.sku.id, quantity: Number(qty) };
      if (cost !== "") out.unit_cost = cost;
      if (line.delivery !== "") out.requested_delivery_date = line.delivery;
      bodyLines.push(out);
    }
    const body: PoCreateBody = {
      supplier_partner_id: supplier.id,
      po_kind: poKind,
      currency,
      payment_terms: { payment_type: payType },
      incoterm: { code: incCode, place: incPlace.trim(), year: Number(incYear) },
      lines: bodyLines,
    };
    if (payType === "TT_ADVANCE") body.payment_terms.advance_pct = advancePct.trim();
    if (payType !== "LC") {
      if (anchor !== "") body.payment_terms.balance_anchor = anchor;
      if (days.trim() !== "") body.payment_terms.balance_days = Number(days.trim());
    }
    if (docDate !== "") body.doc_date = docDate;
    // KRW는 서버가 환율 1·증빙일을 채운다 — 화면이 값을 만들지 않는다.
    if (currency !== "KRW") {
      body.fx_rate = fxRate.trim();
      if (fxDate !== "") body.fx_rate_date = fxDate;
    }
    if (supplierName.trim() !== "") body.supplier_name = supplierName.trim();
    if (note.trim() !== "") body.internal_note = note.trim();
    if (assignee !== "") body.assignee_id = Number(assignee);
    return body;
  }

  function submitPreview() {
    if (previewLock.current || busy) return;
    setFormError(null);
    const body = buildBody();
    if (body === null) return;
    previewLock.current = true; // 동기 잠금 — isPending은 한 틱 늦게 켜진다
    previewMutation.mutate(body, { onSettled: () => (previewLock.current = false) });
  }

  function confirmCreate() {
    if (preview === null || createLock.current) return;
    createLock.current = true;
    const serialized = JSON.stringify(preview.body);
    if (lastSent.current !== null && lastSent.current !== serialized) keyRef.current = crypto.randomUUID();
    lastSent.current = serialized;
    // ★ 미리보기 때의 본문 그대로 — 입력이 그사이 바뀌었어도(수정하러 가면 미리보기가 버려진다) 다른 것을 만들지 않는다.
    createMutation.mutate(preview.body, { onSettled: () => (createLock.current = false) });
  }

  if (!canWrite) {
    return (
      <section>
        <p role="alert" className="break-keep text-signal-red">
          발주는 무역·관리자만 만들 수 있습니다.
        </p>
        <Link to="/purchase-orders" className="mt-3 inline-block text-sm underline">
          발주 목록으로
        </Link>
      </section>
    );
  }

  const userItems = users.data?.items ?? [];
  const activeError = previewMutation.error ?? (confirming ? null : createMutation.error);

  return (
    <section>
      <Link to="/purchase-orders" className="cell-nowrap text-sm text-gray-500 underline">
        ← 발주 목록
      </Link>
      <h1 className="mt-2 text-2xl font-bold">발주 만들기</h1>
      <p className="mt-1 break-keep text-sm text-gray-500">
        입력 → <strong>미리보기</strong>(저장 안 됨) → <strong>발주 확정</strong> 순서입니다. 확정해야 발주번호가 붙고, 그
        즉시 발행·동결됩니다.
      </p>

      {preview === null ? (
        <form
          aria-label="발주 입력"
          className="mt-5 grid gap-5"
          onSubmit={(event) => {
            event.preventDefault();
            submitPreview();
          }}
        >
          <fieldset disabled={previewMutation.isPending} className="contents">
            <section className="rounded-lg border border-gray-200 p-4">
              <h2 className="text-lg font-semibold">공급사·기본 조건</h2>
              <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <div className="flex flex-col gap-1 text-sm">
                  <span className="text-gray-600">공급사 *</span>
                  <SearchSelect<Partner>
                    label="공급사"
                    path="/v1/partners"
                    queryKey={["partners"]}
                    value={supplier}
                    onChange={(next) => {
                      touched();
                      setSupplier(next);
                    }}
                    getKey={(item) => item.id}
                    getLabel={(item) => `${item.name_ko} (${item.partner_code})`}
                    disabled={previewMutation.isPending}
                  />
                  <span className="break-keep text-xs text-gray-500">
                    구매 발주는 공급사 또는 OEM 유형, OEM 생산 발주는 OEM 유형 거래처만 가능합니다(서버가 확인합니다).
                  </span>
                </div>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-gray-600">구분 *</span>
                  <select
                    value={poKind}
                    onChange={(e) => {
                      touched();
                      setPoKind(e.target.value);
                    }}
                    className={inputClass}
                  >
                    {Object.entries(PO_KIND_LABEL).map(([code, label]) => (
                      <option key={code} value={code}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-gray-600">통화 *</span>
                  <select
                    value={currency}
                    onChange={(e) => {
                      touched();
                      setCurrency(e.target.value);
                    }}
                    className={inputClass}
                  >
                    <option value="">선택하세요</option>
                    {currencies.data?.items.map((item) => (
                      <option key={item.code} value={item.code}>
                        {item.code}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-gray-600">증빙일 (비우면 오늘)</span>
                  <input
                    type="date"
                    value={docDate}
                    onChange={(e) => {
                      touched();
                      setDocDate(e.target.value);
                    }}
                    className={inputClass}
                  />
                </label>
                {currency === "KRW" ? (
                  <p className="break-keep text-sm text-gray-500 sm:col-span-2">원화(KRW) 발주는 환율 1로 서버가 채웁니다.</p>
                ) : (
                  <>
                    <label className="flex flex-col gap-1 text-sm">
                      <span className="text-gray-600">환율 *</span>
                      <input
                        inputMode="decimal"
                        value={fxRate}
                        onChange={(e) => {
                          touched();
                          setFxRate(e.target.value);
                        }}
                        className={`${inputClass} text-center`}
                      />
                    </label>
                    <label className="flex flex-col gap-1 text-sm">
                      <span className="text-gray-600">환율 기준일 (비우면 증빙일)</span>
                      <input
                        type="date"
                        value={fxDate}
                        onChange={(e) => {
                          touched();
                          setFxDate(e.target.value);
                        }}
                        className={inputClass}
                      />
                    </label>
                  </>
                )}
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-gray-600">결제조건 *</span>
                  <select
                    value={payType}
                    onChange={(e) => {
                      touched();
                      setPayType(e.target.value);
                    }}
                    className={inputClass}
                  >
                    <option value="">선택하세요</option>
                    {Object.entries(PAYMENT_TYPE_LABEL).map(([code, label]) => (
                      <option key={code} value={code}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                {payType === "TT_ADVANCE" && (
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="text-gray-600">선수금 비율(%) *</span>
                    <input
                      inputMode="decimal"
                      value={advancePct}
                      onChange={(e) => {
                        touched();
                        setAdvancePct(e.target.value);
                      }}
                      className={`${inputClass} text-center`}
                    />
                  </label>
                )}
                {(payType === "TT_ADVANCE" || payType === "TT_DEFERRED") && (
                  <div className="flex gap-2">
                    <label className="flex flex-1 flex-col gap-1 text-sm">
                      <span className="cell-nowrap text-gray-600">잔금 기준</span>
                      <select
                        value={anchor}
                        onChange={(e) => {
                          touched();
                          setAnchor(e.target.value);
                        }}
                        className={inputClass}
                      >
                        <option value="">없음</option>
                        {Object.entries(PO_BALANCE_ANCHOR_LABEL).map(([code, label]) => (
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
                        value={days}
                        onChange={(e) => {
                          touched();
                          setDays(e.target.value);
                        }}
                        className={`${inputClass} text-center`}
                      />
                    </label>
                  </div>
                )}
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-gray-600">인코텀즈 *</span>
                  <select
                    value={incCode}
                    onChange={(e) => {
                      touched();
                      setIncCode(e.target.value);
                    }}
                    className={inputClass}
                  >
                    <option value="">선택하세요</option>
                    {INCOTERM_CODES.map((code) => (
                      <option key={code} value={code}>
                        {code}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-gray-600">인도 장소 *</span>
                  <input
                    value={incPlace}
                    onChange={(e) => {
                      touched();
                      setIncPlace(e.target.value);
                    }}
                    className={inputClass}
                  />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-gray-600">인코텀즈 판(연도)</span>
                  <select
                    value={incYear}
                    onChange={(e) => {
                      touched();
                      setIncYear(e.target.value);
                    }}
                    className={inputClass}
                  >
                    <option value="2020">2020</option>
                    <option value="2010">2010</option>
                  </select>
                </label>
              </div>
            </section>

            <section className="rounded-lg border border-gray-200 p-4">
              <h2 className="text-lg font-semibold">공급사명·메모·담당자</h2>
              <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-gray-600">공급사명 표기 (비우면 거래처 영문명·국문명)</span>
                  <input
                    value={supplierName}
                    maxLength={200}
                    onChange={(e) => {
                      touched();
                      setSupplierName(e.target.value);
                    }}
                    className={inputClass}
                  />
                  <span className="break-keep text-xs text-signal-red">{NO_COST_IN_FREE_TEXT}</span>
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-gray-600">내부 메모 (선택)</span>
                  <textarea
                    value={note}
                    maxLength={1000}
                    rows={2}
                    onChange={(e) => {
                      touched();
                      setNote(e.target.value);
                    }}
                    className={inputClass}
                  />
                  <span className="break-keep text-xs text-signal-red">{NO_COST_IN_FREE_TEXT}</span>
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-gray-600">담당자 (비우면 나)</span>
                  <select
                    value={assignee}
                    onChange={(e) => {
                      touched();
                      setAssignee(e.target.value);
                    }}
                    className={inputClass}
                  >
                    <option value="">나</option>
                    {userItems.map((user) => (
                      <option key={user.id} value={user.id}>
                        {user.display_name}
                      </option>
                    ))}
                  </select>
                  <span className="break-keep text-xs text-gray-500">
                    무역·관리자 역할 사용자만 담당자가 될 수 있습니다(서버가 확인합니다).
                  </span>
                  {users.data && users.data.total > userItems.length && (
                    <span role="status" className="text-xs text-gray-500">
                      {users.data.total}명 중 {userItems.length}명만 표시합니다.
                    </span>
                  )}
                </label>
              </div>
            </section>

            <section aria-labelledby="po-lines-title" className="rounded-lg border border-gray-200 p-4">
              <h2 id="po-lines-title" className="text-lg font-semibold">
                라인
              </h2>
              <p className="mt-1 break-keep text-sm text-gray-500">
                단가를 비워 두면 증빙일 기준 마스터 매입가가 적용되고, 입력하면 수동 단가가 됩니다. 마스터 매입가가 없으면 서버가
                알려 줍니다.
              </p>
              <div className="mt-3 overflow-x-auto rounded border border-gray-200">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-left text-gray-600">
                    <tr>
                      <th className="cell-nowrap px-3 py-2 text-center">번호</th>
                      <th className="cell-nowrap px-3 py-2">SKU</th>
                      <th className="cell-nowrap px-3 py-2 text-center">수량</th>
                      <th className="cell-nowrap px-3 py-2 text-center">단가 (비우면 마스터)</th>
                      <th className="cell-nowrap px-3 py-2 text-center">요청납기</th>
                      <th className="cell-nowrap px-3 py-2 text-center">삭제</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lines.map((line, index) => (
                      <tr key={line.key} className="border-t border-gray-100 align-top">
                        <td className="num cell-nowrap px-3 py-2">{index + 1}</td>
                        <td className="min-w-64 px-3 py-2">
                          <SearchSelect<Sku>
                            label={`라인 ${index + 1} SKU`}
                            path="/v1/skus"
                            queryKey={["skus"]}
                            value={line.sku}
                            onChange={(next) => updateLine(line.key, { sku: next })}
                            getKey={(item) => item.id}
                            getLabel={(item) => `${item.sku_code} ${item.name_ko}`}
                            disabled={previewMutation.isPending}
                          />
                        </td>
                        <td className="px-3 py-2 text-center">
                          <input
                            inputMode="numeric"
                            aria-label={`라인 ${index + 1} 수량`}
                            value={line.qty}
                            onChange={(e) => updateLine(line.key, { qty: e.target.value })}
                            className="num w-24 rounded border border-gray-300 px-2 py-1"
                          />
                        </td>
                        <td className="px-3 py-2 text-center">
                          <input
                            inputMode="decimal"
                            aria-label={`라인 ${index + 1} 단가`}
                            value={line.cost}
                            onChange={(e) => updateLine(line.key, { cost: e.target.value })}
                            className="num w-32 rounded border border-gray-300 px-2 py-1"
                          />
                        </td>
                        <td className="px-3 py-2 text-center">
                          <input
                            type="date"
                            aria-label={`라인 ${index + 1} 요청납기`}
                            value={line.delivery}
                            onChange={(e) => updateLine(line.key, { delivery: e.target.value })}
                            className="rounded border border-gray-300 px-2 py-1"
                          />
                        </td>
                        <td className="px-3 py-2 text-center">
                          <button
                            type="button"
                            aria-label={`라인 ${index + 1} 삭제`}
                            disabled={lines.length <= 1}
                            onClick={() => {
                              touched();
                              setLines((prev) => prev.filter((item) => item.key !== line.key));
                            }}
                            className="cell-nowrap rounded border border-gray-300 px-2 py-1 disabled:opacity-40"
                          >
                            삭제
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <button
                type="button"
                disabled={lines.length >= MAX_LINES}
                onClick={() => {
                  touched();
                  setLines((prev) => [...prev, newLine()]);
                }}
                className="cell-nowrap mt-3 rounded border border-gray-300 px-3 py-2 text-sm disabled:opacity-50"
              >
                라인 추가
              </button>
            </section>
          </fieldset>

          {(activeError || formError) && (
            <div role="alert" className="break-keep text-sm text-signal-red">
              {formError ?? errorMessage(activeError, "발주를 처리하지 못했습니다.", "발주")}
            </div>
          )}

          <div className="flex justify-end gap-2">
            <Link to="/purchase-orders" className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm">
              닫기
            </Link>
            <button
              type="submit"
              disabled={previewMutation.isPending}
              className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              {previewMutation.isPending ? "계산 중…" : "미리보기"}
            </button>
          </div>
        </form>
      ) : (
        <PreviewPanel
          data={preview.data}
          pending={createMutation.isPending}
          error={createMutation.error && !confirming ? errorMessage(createMutation.error, "발주를 만들지 못했습니다.", "발주") : null}
          onBack={() => {
            // 수정하러 돌아간다 — 본문이 달라질 수 있으니 미리보기·오류를 버린다(키는 확정 시 본문 비교로 정한다).
            setPreview(null);
            setConfirming(false);
            touched();
          }}
          onConfirm={() => {
            createMutation.reset();
            setConfirming(true);
          }}
        />
      )}

      {confirming && preview !== null && (
        <ConfirmDialog
          title="발주를 확정할까요?"
          danger
          confirmLabel="발주 확정"
          description={
            <>
              <p>
                <strong>{preview.data.supplier_name}</strong>에 대한 발주{" "}
                <strong>
                  {preview.data.total_text} {preview.data.currency}
                </strong>
                (라인 {preview.data.lines.length}개)를 만듭니다.
              </p>
              <p className="mt-2">{PO_RISK_NOTICE}</p>
            </>
          }
          pending={createMutation.isPending}
          error={createMutation.error ? errorMessage(createMutation.error, "발주를 만들지 못했습니다.", "발주") : null}
          onConfirm={confirmCreate}
          onCancel={() => {
            if (createMutation.isPending) return;
            createMutation.reset();
            setConfirming(false);
          }}
        />
      )}
    </section>
  );
}

function PreviewPanel({
  data,
  pending,
  error,
  onBack,
  onConfirm,
}: {
  data: PurchaseOrderPreview;
  pending: boolean;
  error: string | null;
  onBack: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="mt-5">
      <h2 className="text-lg font-semibold">발주 미리보기</h2>
      <p role="status" className="mt-1 break-keep rounded border border-gray-400 p-2 text-sm">
        아직 저장되지 않았습니다. <strong>'발주 확정'</strong>을 누르면 발주번호가 붙고 바로 발행·동결되어 가격·조건·라인을
        고칠 수 없습니다. 마스터 매입가를 쓴 라인의 단가는 확정하는 순간 서버가 증빙일 기준으로 다시 읽습니다.
      </p>

      <dl className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <DocField label="공급사">{data.supplier_name}</DocField>
        <DocField label="구분">{PO_KIND_LABEL[data.po_kind] ?? data.po_kind}</DocField>
        <DocField label="통화">{data.currency}</DocField>
        <DocField label="증빙일">{data.doc_date}</DocField>
        <DocField label="환율">
          {data.fx_rate === null ? EMPTY : `${data.fx_rate} (기준일 ${show(data.fx_rate_date)})`}
        </DocField>
        <DocField label="결제조건">{paymentTermsText(data.payment_terms)}</DocField>
        <DocField label="인코텀즈">{incotermText(data.incoterm)}</DocField>
        <DocField label="내부 메모">{show(data.internal_note)}</DocField>
      </dl>

      <div className="mt-4 overflow-x-auto rounded-lg border border-gray-200">
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
            </tr>
          </thead>
          <tbody>
            {data.lines.map((line) => (
              <tr key={line.line_no} className="border-t border-gray-100 align-top">
                <td className="num cell-nowrap px-3 py-2">{line.line_no}</td>
                <td className="cell-nowrap px-3 py-2">{line.sku_code}</td>
                <td className="break-keep px-3 py-2">{line.sku_name_ko}</td>
                <td className="num cell-nowrap px-3 py-2">{line.quantity}</td>
                <td className="num cell-nowrap px-3 py-2">{line.requested_delivery_date ?? EMPTY}</td>
                <td className="num cell-nowrap px-3 py-2">{line.unit_cost_text}</td>
                <td className="cell-nowrap px-3 py-2 text-center">
                  {PRICE_BASIS_LABEL[line.price_basis] ?? line.price_basis}
                </td>
                <td className="num cell-nowrap px-3 py-2">{line.line_cost_text}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-gray-200 bg-gray-50 font-semibold">
              <td colSpan={7} className="cell-nowrap px-3 py-2 text-right">
                합계 (서버 계산)
              </td>
              <td className="num cell-nowrap px-3 py-2">
                {data.total_text} {data.currency}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      {error && (
        <p role="alert" className="mt-3 break-keep text-sm text-signal-red">
          {error}
        </p>
      )}

      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <button
          type="button"
          onClick={onBack}
          disabled={pending}
          className="cell-nowrap rounded border border-gray-900 px-4 py-2 text-sm disabled:opacity-50"
        >
          입력 수정
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={pending}
          className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
        >
          발주 확정…
        </button>
      </div>
    </div>
  );
}
