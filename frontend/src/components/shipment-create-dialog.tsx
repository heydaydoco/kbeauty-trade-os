// 수주 → 수출선적 만들기 2단 대화상자 (S3-2 PR-3b — design-D D7·D4·D10·D13 / PROGRESS 'S3-2 PR-3a' 인계 계약 S3·S4).
//
// 흐름: ① 입력(라인별 이번 선적 수량·출발/도착국·당사자·메모) → ② `/preview`(저장 0 — 채번·이벤트·키 소비 없음)를 서버 계산값 그대로
//       → ③ '생성 확정'(미리보기 때의 본문 그대로 POST, 성공하면 선적 상세로).
// 규칙:
// - 본문에는 원천 값(SKU·단가·통화·환율·조건·거래 상대) 필드가 없다 — 재입력 화면이 없다(서버 extra=forbid가 구조로 보증).
// - 이번 수량 기본값은 **빈칸**(실수 한 번에 전량 선적 방지 — 부분선적이 기본 업무). 행마다 [잔량 전부]. 빈칸·0인 행은 본문에서 뺀다.
// - 출발·도착국은 **기본값 없음·필수**(시장 코드로 미리 채우지 않는다 — 시장 ≠ 도착항 국가, design-D D7).
// - 잔량 초과는 화면 사전 검사(보이는 잔량 기준) + 서버 409 `EXCEEDS_OPEN`의 `detail.open_quantity`를 **해당 라인 칸 아래에** 줄별로.
// - 멱등 키는 대화상자를 여는 순간 1개 — 같은 본문 재확정은 같은 키, 본문이 실제로 달라질 때만 새 키. 더블클릭은 동기 잠금(ref).
// - 가용재고는 '미산정' 배지(§8.3 자리 — 숫자·0 표시 금지). DG는 배지만(차단 0).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useDialogBehavior } from "./confirm-dialog";
import { DocField, incotermText, paymentTermsText, show } from "./proforma-facts";
import { SearchSelect } from "./search-select";
import { ApiError, apiFetch } from "../lib/api";
import { errorMessage, isVersionConflict } from "../lib/api-errors";
import { ORDER_BOARD_QUERY_KEY } from "../lib/order-board";
import { DOCUMENT_FLOW_QUERY_KEY, SALES_ORDERS_QUERY_KEY, salesOrderDetailKey, type SalesOrderDetail } from "../lib/sales-order";
import {
  LOCK_BUSY_CODE,
  PARTY_PARTNER_TYPE,
  SELECTABLE_PARTY_ROLES,
  SHIPMENTS_QUERY_KEY,
  countryText,
  createKeyKeeper,
  openQuantityByLine,
  partyRoleLabel,
  type SelectablePartyRole,
  type ShipmentCreateBody,
  type ShipmentDetail,
  type ShipmentPreview,
} from "../lib/shipment";
import type { Partner } from "../routes/partners";
import { AvailabilityBadge, CountryInput, DgBadge, PartnerFixHint, countryProblem } from "./shipment-parts";

const MAX_QUANTITY = 99_999_999; // 서버 수량 상한과 같은 형식 검증용

interface Props {
  so: SalesOrderDetail;
  onClose: () => void;
  /** 원천 수주가 그 사이 바뀜(409 상태) — 부모가 닫고 다시 불러온다. */
  onReload: () => void;
}

type Parties = Record<SelectablePartyRole, Partner | null>;

/** 라인 칸 문제 — 빈칸·0은 '이번엔 안 실음'(문제 아님), 그 밖은 정수·보이는 잔량 이내. */
function lineProblem(raw: string, open: number): string | null {
  const value = raw.trim();
  if (value === "" || value === "0") return null;
  if (!/^[1-9][0-9]*$/.test(value) || Number(value) > MAX_QUANTITY) return "1 이상의 정수로 입력해 주세요.";
  if (Number(value) > open) return `남은 수량(${open})을 넘습니다.`;
  return null;
}

export function ShipmentCreateDialog({ so, onClose, onReload }: Props) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const titleId = useId();

  const [qty, setQty] = useState<Record<number, string>>(() => Object.fromEntries(so.lines.map((line) => [line.id, ""])));
  const [origin, setOrigin] = useState("");
  const [dest, setDest] = useState("");
  const [parties, setParties] = useState<Parties>({ NOTIFY: null, FORWARDER: null, CUSTOMS_BROKER: null });
  const [note, setNote] = useState("");
  const [triedPreview, setTriedPreview] = useState(false);
  // 서버 409 EXCEEDS_OPEN의 라인별 남은 수량 — 1단 칸 아래에 붙인다(입력을 고치면 그 칸의 안내만 지운다).
  const [serverOpen, setServerOpen] = useState<Map<number, number>>(new Map());
  const [preview, setPreview] = useState<{ data: ShipmentPreview; body: ShipmentCreateBody } | null>(null);

  const boxRef = useRef<HTMLDivElement | null>(null);
  const previewLock = useRef(false);
  const createLock = useRef(false);
  const [keys] = useState(() => createKeyKeeper()); // 대화상자 1회 열림 = 키 1개

  function afterError(error: unknown) {
    const open = openQuantityByLine(error);
    if (open.size > 0) {
      setServerOpen(open);
      setPreview(null); // 1단으로 돌아가 칸별 잔량을 보인다
      // 화면의 '남은 잔량'도 서버 값으로 다시 받는다 — 옛 잔량(경쟁 선적 전)이 칸 옆에 남아 서버 안내와 엇갈리지 않게
      // (실브라우저 관통 발견: 409 뒤 '남은 잔량 4' 옆에 '서버 확인: 남은 수량 2'). 입력값은 라인 id로 묶여 그대로 남는다.
      void client.invalidateQueries({ queryKey: salesOrderDetailKey(so.id), exact: true });
    }
  }

  const previewMutation = useMutation({
    mutationFn: (body: ShipmentCreateBody) =>
      apiFetch<ShipmentPreview>(`/v1/sales-orders/${so.id}/shipments/preview`, { method: "POST", body }),
    onSuccess: (data, body) => setPreview({ data, body }),
    onError: afterError,
  });

  const createMutation = useMutation({
    mutationFn: (input: { key: string; body: ShipmentCreateBody }) =>
      apiFetch<ShipmentDetail>(`/v1/sales-orders/${so.id}/shipments`, {
        method: "POST",
        idempotencyKey: input.key,
        body: input.body,
      }),
    onSuccess: (created) => {
      // 첫 선적이면 수주가 같은 트랜잭션에서 '선적중'이 된다 — 수주·보드·흐름·선적 목록을 다시 불러오게 한다.
      void client.invalidateQueries({ queryKey: salesOrderDetailKey(so.id) });
      void client.invalidateQueries({ queryKey: [...SALES_ORDERS_QUERY_KEY, "list"] });
      void client.invalidateQueries({ queryKey: SHIPMENTS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: DOCUMENT_FLOW_QUERY_KEY });
      void client.invalidateQueries({ queryKey: ORDER_BOARD_QUERY_KEY });
      void navigate(`/shipments/${created.id}`);
    },
    onError: afterError,
  });

  const busy = previewMutation.isPending || createMutation.isPending;
  useDialogBehavior(boxRef, () => {
    if (!busy) onClose();
  });

  function touched() {
    previewMutation.reset();
    createMutation.reset();
  }

  const lineProblems = new Map<number, string>();
  for (const line of so.lines) {
    const problem = lineProblem(qty[line.id] ?? "", line.shipment_open_quantity ?? 0);
    if (problem !== null) lineProblems.set(line.id, problem);
  }
  const chosenLines = so.lines.filter((line) => /^[1-9][0-9]*$/.test((qty[line.id] ?? "").trim()));
  const originProblem = countryProblem(origin);
  const destProblem = countryProblem(dest);
  const canPreview = chosenLines.length > 0 && lineProblems.size === 0 && originProblem === null && destProblem === null;

  function buildBody(): ShipmentCreateBody {
    const body: ShipmentCreateBody = {
      lines: chosenLines.map((line) => ({ so_line_id: line.id, quantity: Number((qty[line.id] ?? "").trim()) })),
      origin_country_code: origin,
      dest_country_code: dest,
    };
    const chosenParties = SELECTABLE_PARTY_ROLES.flatMap((role) => {
      const partner = parties[role];
      return partner === null ? [] : [{ role, partner_id: partner.id }];
    });
    if (chosenParties.length > 0) body.parties = chosenParties;
    if (note.trim() !== "") body.internal_note = note.trim();
    return body;
  }

  function submitPreview() {
    setTriedPreview(true);
    if (!canPreview || previewLock.current) return;
    previewLock.current = true; // 동기 잠금
    setServerOpen(new Map());
    previewMutation.mutate(buildBody(), { onSettled: () => (previewLock.current = false) });
  }

  function confirmCreate() {
    if (preview === null || createLock.current) return;
    createLock.current = true;
    // ★ 미리보기 때의 본문 그대로 — 같은 본문 재확정은 같은 키(결과를 모르는 실패 뒤 중복 선적 방지), 다르면 새 키.
    const key = keys.keyFor(JSON.stringify(preview.body));
    createMutation.mutate({ key, body: preview.body }, { onSettled: () => (createLock.current = false) });
  }

  const activeError = createMutation.error ?? previewMutation.error;
  const exceeded = openQuantityByLine(activeError).size > 0;

  const errorBlock = activeError !== null && (
    <div role="alert" className="mt-3 text-sm text-signal-red">
      <p className="break-keep">
        {exceeded
          ? "다른 선적이 먼저 가져가 남은 수량이 줄었습니다. 라인별 남은 수량을 확인해 수량을 고쳐 주세요."
          : errorMessage(activeError, "선적을 만들지 못했습니다.", "수주")}
      </p>
      <PartnerFixHint error={activeError} />
      {activeError instanceof ApiError && activeError.code === LOCK_BUSY_CODE && (
        <p className="mt-1 break-keep">잠시 후 같은 버튼을 다시 눌러 주세요(같은 요청으로 다시 보냅니다).</p>
      )}
      {(isVersionConflict(activeError) ||
        (activeError instanceof ApiError && activeError.code === "TRADE_DOCS.QUANTITY.DOCUMENT_NOT_CONSUMABLE")) && (
        <button type="button" onClick={onReload} className="cell-nowrap mt-2 rounded border border-signal-red px-3 py-1">
          수주 다시 불러오기
        </button>
      )}
    </div>
  );

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4">
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="max-h-[90vh] w-full max-w-[calc(100vw-2rem)] overflow-y-auto rounded-lg bg-white p-5 shadow-lg sm:max-w-3xl"
      >
        {preview === null ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              submitPreview();
            }}
          >
            <h2 id={titleId} className="text-lg font-bold">
              선적 만들기 — {so.doc_number}
            </h2>
            <p className="mt-1 break-keep text-sm text-gray-500">
              통화·환율·결제조건·인코텀즈·품목·단가·바이어는 수주에서 그대로 복사됩니다(다시 입력하지 않습니다). 만들면 <strong>계획</strong>{" "}
              상태로 시작하고, 첫 선적이면 수주가 &lsquo;선적중&rsquo;이 됩니다.
            </p>

            <h3 className="mt-4 text-sm font-semibold">이번 선적 수량</h3>
            <p className="break-keep text-xs text-gray-500">빈칸·0인 라인은 이번 선적에 싣지 않습니다. 남은 수량 안에서 나눠 실을 수 있습니다.</p>
            <ul className="mt-2 grid gap-2" aria-label="선적할 라인">
              {so.lines.map((line) => {
                const open = line.shipment_open_quantity ?? 0;
                const problem = lineProblems.get(line.id) ?? null;
                const server = serverOpen.get(line.id);
                const hintId = `ship-line-${line.id}-hint`;
                return (
                  <li key={line.id} className="rounded border border-gray-200 p-3 text-sm">
                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                      <span className="num cell-nowrap text-gray-500">#{line.line_no}</span>
                      <span className="cell-nowrap font-medium">{line.sku_code}</span>
                      <span className="break-keep">{line.sku_name_ko}</span>
                    </div>
                    <div className="mt-2 flex flex-wrap items-end gap-3">
                      <span className="cell-nowrap text-gray-600">
                        수주 <span className="num">{line.quantity}</span>
                      </span>
                      <span className="cell-nowrap text-gray-600">
                        남은 잔량 <span className="num font-medium">{open}</span>
                      </span>
                      <label className="flex flex-col gap-1">
                        <span className="cell-nowrap text-gray-600">이번 선적 수량</span>
                        <input
                          inputMode="numeric"
                          aria-label={`라인 ${line.line_no} 이번 선적 수량`}
                          aria-invalid={problem !== null || server !== undefined}
                          aria-describedby={hintId}
                          value={qty[line.id] ?? ""}
                          disabled={open <= 0}
                          onChange={(event) => {
                            touched();
                            setServerOpen((prev) => {
                              const next = new Map(prev);
                              next.delete(line.id);
                              return next;
                            });
                            setQty((prev) => ({ ...prev, [line.id]: event.target.value }));
                          }}
                          className="w-28 rounded border border-gray-300 px-2 py-1 text-center disabled:bg-gray-100"
                        />
                      </label>
                      <button
                        type="button"
                        disabled={open <= 0}
                        aria-label={`라인 ${line.line_no} 잔량 전부`}
                        onClick={() => {
                          touched();
                          setServerOpen((prev) => {
                            const next = new Map(prev);
                            next.delete(line.id);
                            return next;
                          });
                          setQty((prev) => ({ ...prev, [line.id]: String(open) }));
                        }}
                        className="cell-nowrap rounded border border-gray-300 px-3 py-1 disabled:opacity-50"
                      >
                        잔량 전부
                      </button>
                      <AvailabilityBadge />
                    </div>
                    <p id={hintId} className="mt-1 break-keep text-xs">
                      {server !== undefined ? (
                        <span role="alert" className="text-signal-red">
                          서버 확인: 남은 수량 {server} — 수량을 {server} 이하로 고쳐 주세요.
                        </span>
                      ) : problem !== null ? (
                        <span className="text-signal-red">{problem}</span>
                      ) : open <= 0 ? (
                        <span className="text-gray-500">남은 수량이 없습니다(이미 다른 선적에 모두 배정).</span>
                      ) : null}
                    </p>
                  </li>
                );
              })}
            </ul>

            <div className="mt-4 flex flex-wrap gap-4">
              <CountryInput
                label="출발국"
                value={origin}
                onChange={(next) => {
                  touched();
                  setOrigin(next);
                }}
                problem={originProblem}
                showProblem={triedPreview || origin !== ""}
              />
              <CountryInput
                label="도착국"
                value={dest}
                onChange={(next) => {
                  touched();
                  setDest(next);
                }}
                problem={destProblem}
                showProblem={triedPreview || dest !== ""}
              />
            </div>

            <fieldset className="mt-4 text-sm">
              <legend className="font-semibold">당사자 (선택)</legend>
              <p className="break-keep text-xs text-gray-500">
                수하인은 수주 바이어의 영문 이름·주소가 자동으로 복사됩니다(자동 — 여기서 지정하지 않습니다).
              </p>
              <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-3">
                {SELECTABLE_PARTY_ROLES.map((role) => {
                  const type = PARTY_PARTNER_TYPE[role];
                  return (
                    <div key={role} className="flex flex-col gap-1">
                      <span className="cell-nowrap text-gray-600">{partyRoleLabel(role)}</span>
                      <SearchSelect<Partner>
                        label={`${partyRoleLabel(role)} 거래처`}
                        path="/v1/partners"
                        params={type === null ? undefined : { type }}
                        queryKey={["partners"]}
                        value={parties[role]}
                        onChange={(next) => {
                          touched();
                          setParties((prev) => ({ ...prev, [role]: next }));
                        }}
                        getKey={(item) => item.id}
                        getLabel={(item) => `${item.name_ko}${item.name_en ? ` (${item.name_en})` : " — 영문명 없음"}`}
                      />
                    </div>
                  );
                })}
              </div>
            </fieldset>

            <label className="mt-4 flex flex-col gap-1 text-sm">
              <span className="text-gray-600">내부 메모 (선택)</span>
              <input
                value={note}
                maxLength={1000}
                onChange={(event) => {
                  touched();
                  setNote(event.target.value);
                }}
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>

            {errorBlock}

            <div className="mt-5 flex flex-wrap items-center justify-end gap-2">
              {chosenLines.length === 0 && (
                <span className="break-keep text-xs text-gray-500">선적할 라인 수량을 하나 이상 입력하세요.</span>
              )}
              <button type="button" onClick={onClose} disabled={busy} className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm disabled:opacity-50">
                닫기
              </button>
              <button
                type="submit"
                disabled={busy || chosenLines.length === 0}
                className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                {previewMutation.isPending ? "확인 중…" : "다음: 미리보기"}
              </button>
            </div>
          </form>
        ) : (
          <div>
            <h2 id={titleId} className="text-lg font-bold">
              선적을 만들까요? — {preview.data.so_doc_number}
            </h2>
            <p className="mt-1 break-keep text-sm text-gray-500">
              아래는 서버가 계산한 미리보기입니다(아직 저장되지 않았습니다). &lsquo;생성 확정&rsquo;을 누르면 <strong>계획</strong> 상태의 선적이
              만들어지고 선적번호가 붙습니다.
            </p>
            <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <DocField label="거래 상대">{preview.data.counterparty.name}</DocField>
              <DocField label="출발국 → 도착국">
                <span className="cell-nowrap">{countryText(preview.data.origin_country_code)}</span>
                {" → "}
                <span className="cell-nowrap">{countryText(preview.data.dest_country_code)}</span>
              </DocField>
              <DocField label="증빙일">
                <span className="num cell-nowrap">{preview.data.doc_date}</span>
              </DocField>
              <DocField label="통화·고정 환율">
                <span className="num cell-nowrap">
                  {preview.data.currency} · {show(preview.data.fx_rate)} (기준일 {show(preview.data.fx_rate_date)})
                </span>
              </DocField>
              <DocField label="결제조건">{paymentTermsText(preview.data.payment_terms)}</DocField>
              <DocField label="인코텀즈">{incotermText(preview.data.incoterm)}</DocField>
            </dl>
            <div className="mt-3 overflow-x-auto rounded border border-gray-200">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-left text-gray-600">
                  <tr>
                    <th scope="col" className="cell-nowrap px-3 py-2 text-center">번호</th>
                    <th scope="col" className="cell-nowrap px-3 py-2">SKU</th>
                    <th scope="col" className="cell-nowrap min-w-32 px-3 py-2">품명</th>
                    <th scope="col" className="cell-nowrap px-3 py-2 text-center">이번 수량</th>
                    <th scope="col" className="cell-nowrap px-3 py-2 text-center">선적 전 잔량</th>
                    <th scope="col" className="cell-nowrap px-3 py-2 text-center">선적 후 잔량</th>
                    <th scope="col" className="cell-nowrap px-3 py-2 text-center">단가</th>
                    <th scope="col" className="cell-nowrap px-3 py-2 text-center">금액</th>
                    <th scope="col" className="cell-nowrap px-3 py-2 text-center">DG</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.data.lines.map((line) => (
                    <tr key={line.so_line_id} className="border-t border-gray-100">
                      <td className="num cell-nowrap px-3 py-2">{line.line_no}</td>
                      <td className="cell-nowrap px-3 py-2">{line.sku.code}</td>
                      <td className="break-keep px-3 py-2">{line.sku.name_ko}</td>
                      <td className="num cell-nowrap px-3 py-2">{line.quantity}</td>
                      <td className="num cell-nowrap px-3 py-2">{line.open_quantity_before}</td>
                      <td className="num cell-nowrap px-3 py-2">{line.remaining_after}</td>
                      <td className="num cell-nowrap px-3 py-2">{line.is_free ? "무상" : line.unit_price_text}</td>
                      <td className="num cell-nowrap px-3 py-2">{line.line_amount_text}</td>
                      <td className="px-3 py-2 text-center">
                        <DgBadge dg={line.dg} />
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t border-gray-200 bg-gray-50 font-semibold">
                    <td colSpan={7} className="cell-nowrap px-3 py-2 text-right">
                      합계 (서버 계산)
                    </td>
                    <td className="num cell-nowrap px-3 py-2">
                      {preview.data.total_text} {preview.data.currency}
                    </td>
                    <td />
                  </tr>
                </tfoot>
              </table>
            </div>
            <h3 className="mt-4 text-sm font-semibold">당사자</h3>
            <ul className="mt-1 grid gap-1 text-sm">
              {preview.data.parties.map((party) => (
                <li key={`${party.role}-${party.partner_id}`} className="break-keep">
                  <span className="cell-nowrap font-medium">{partyRoleLabel(party.role)}</span> {party.name_en}
                  {party.auto && <span className="ml-2 cell-nowrap text-xs text-gray-500">(자동 — 수주 바이어)</span>}
                </li>
              ))}
            </ul>

            {errorBlock}

            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  createMutation.reset();
                  setPreview(null);
                }}
                className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm disabled:opacity-50"
              >
                뒤로
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={confirmCreate}
                className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                {createMutation.isPending ? "만드는 중…" : "생성 확정"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
