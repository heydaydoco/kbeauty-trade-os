// 원천 전표 참조 선적 만들기 2단 대화상자 — 수주 → 수출선적(S3-2 PR-3b — design-D D7·D4·D10·D13 / PROGRESS 'S3-2 PR-3a' 인계 계약 S3·S4)과
// 발주 → 수입선적(S3-2 PR-5b — PROGRESS 'S3-2 PR-5a' 인계 계약)이 **같은 흐름 코어**(`ReferenceShipmentDialog`)를 쓴다. 차이는 `ShipmentFlavor`
// (경로·본문 라인 키·남은 양의 이름·409 코드·재조회 대상·문구·2단 미리보기 표)뿐이다.
//
// 흐름: ① 입력(라인별 이번 선적 수량·출발/도착국·당사자·메모) → ② `/preview`(저장 0 — 채번·이벤트·키 소비 없음)를 서버 계산값 그대로
//       → ③ '생성 확정'(미리보기 때의 본문 그대로 POST, 성공하면 선적 상세로).
// 규칙:
// - 본문에는 원천 값(SKU·단가·통화·환율·조건·거래 상대) 필드가 없다 — 재입력 화면이 없다(서버 extra=forbid가 구조로 보증).
// - 이번 수량 기본값은 **빈칸**(실수 한 번에 전량 선적 방지 — 부분선적이 기본 업무). 행마다 [잔량 전부]. 빈칸·0인 행은 본문에서 뺀다.
// - 출발·도착국은 **기본값 없음·필수**(시장 코드로 미리 채우지 않는다 — 시장 ≠ 도착항 국가, design-D D7).
// - 남은 양 초과는 화면 사전 검사(보이는 값 기준) + 서버 409의 라인별 값(수출 `EXCEEDS_OPEN.open_quantity` / 수입
//   `EXCEEDS_ASSIGNABLE.assignable_quantity`)을 **해당 라인 칸 아래에** 줄별로. 422 `detail["lines[i]…"]`·`detail.internal_note`도 그 칸 아래에.
// - 멱등 키는 대화상자를 여는 순간 1개 — 같은 본문 재확정은 같은 키, 본문이 실제로 달라질 때만 새 키. 더블클릭은 동기 잠금(ref).
// - 가용재고는 '미산정' 배지(§8.3 자리 — 숫자·0 표시 금지). DG는 배지만(차단 0).
// - (적대 검토 반영) 409 뒤 재조회로 남은 양이 0이 된 라인은 수량을 자동으로 비우고 알린다(막다른 길 0) / 미리보기 대기 중 1단 입력 잠금·
//   보낸 본문과 지금 본문이 다르면 늦게 온 응답을 버린다 / 결과를 모르는 실패(0·5xx)는 같은 키 재확정 안내·'뒤로'·Esc 막음 /
//   단계가 바뀌면 맨 위로 스크롤하고 단계 제목(409 복귀는 첫 문제 칸)에 포커스 / 동기 잠금은 요청 finally에서 푼다.
// - ★ (PR-5b) 수입선적 미리보기·생성 응답엔 금액·통화 키가 없다 — 수입 2단 표에는 단가·금액·합계·통화·환율 칸을 **두지 않는다**(원가 열람 역할도).

import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { useDialogBehavior } from "./confirm-dialog";
import { DocField, incotermText, paymentTermsText, show } from "./proforma-facts";
import { SearchSelect } from "./search-select";
import { ApiError, apiFetch } from "../lib/api";
import { errorMessage, isVersionConflict } from "../lib/api-errors";
import { ORDER_BOARD_QUERY_KEY } from "../lib/order-board";
import { PURCHASE_ORDERS_QUERY_KEY, purchaseOrderDetailKey, type PurchaseOrderDetail } from "../lib/purchase-order";
import { DOCUMENT_FLOW_QUERY_KEY, SALES_ORDERS_QUERY_KEY, salesOrderDetailKey, type SalesOrderDetail } from "../lib/sales-order";
import {
  LOCK_BUSY_CODE,
  PARTY_PARTNER_TYPE,
  SELECTABLE_PARTY_ROLES,
  SHIPMENTS_QUERY_KEY,
  assignableByLine,
  countryText,
  createKeyKeeper,
  isResultUnknown,
  lineFieldErrors,
  openQuantityByLine,
  partyRoleLabel,
  poShipmentsKey,
  refreshAfterAssignableConflict,
  refreshAfterQuantityConflict,
  soShipmentsKey,
  type ImportShipmentCreateBody,
  type ImportShipmentPreview,
  type SelectablePartyRole,
  type ShipmentCreateBody,
  type ShipmentDetail,
  type ShipmentPreview,
  type ShipmentPreviewParty,
} from "../lib/shipment";
import type { Partner } from "../routes/partners";
import { AvailabilityBadge, CountryInput, DgBadge, PartnerFixHint, countryProblem } from "./shipment-parts";

const MAX_QUANTITY = 99_999_999; // 서버 수량 상한과 같은 형식 검증용
const DOCUMENT_NOT_CONSUMABLE_CODE = "TRADE_DOCS.QUANTITY.DOCUMENT_NOT_CONSUMABLE";

type Parties = Record<SelectablePartyRole, Partner | null>;

/** 대화상자가 보는 원천 라인 — 남은 양(`open`)은 서버 파생값(수출 = 선적 잔량, 수입 = 배정 가능량 — 화면 산술 0). */
interface SourceLine {
  id: number;
  line_no: number;
  sku_code: string;
  sku_name_ko: string;
  quantity: number;
  open: number;
}

/** 본문의 공통 부분 — 라인 키만 원천마다 다르다(수출 `so_line_id`·수입 `po_line_id`). */
interface CommonBody {
  origin_country_code: string;
  dest_country_code: string;
  parties?: Array<{ role: SelectablePartyRole; partner_id: number }>;
  internal_note?: string;
}

/** 원천 전표별 차이 — 흐름(잠금·키·늦은 응답·자동 비움·포커스·결과 불명)은 코어 한 벌, 여기는 값·문구·표뿐. */
/** 두 미리보기 응답 공통 — 당사자 목록(수출 자동 수하인·수입 자동 송하인). */
interface PreviewWithParties {
  parties: ShipmentPreviewParty[];
}

interface ShipmentFlavor<B extends CommonBody, P extends PreviewWithParties> {
  docNumber: string;
  previewPath: string;
  createPath: string;
  lines: SourceLine[];
  buildBody: (chosen: Array<{ id: number; quantity: number }>, common: CommonBody) => B;
  /** 보낸 본문의 라인 순서대로 원천 라인 id — 422 `lines[i]` 칸 오류를 라인에 붙인다. */
  lineIdsOf: (body: B) => number[];
  /** 수량 초과 409의 라인별 남은 양(코드가 다르면 빈 맵 — 수출 잔량·수입 배정 가능량을 섞지 않는다). */
  conflictByLine: (error: unknown) => Map<number, number>;
  refreshAfterConflict: (client: QueryClient) => void;
  afterCreated: (client: QueryClient) => void;
  /** 결과 불명인 채 떠날 때 — 이 원천의 선적 목록·원천 상세를 다시 받아 생성 여부를 화면에서 확인하게 한다. */
  refreshOnLeave: (client: QueryClient) => void;
  previewDocNumber: (data: P) => string;
  renderPreview: (data: P) => ReactNode;
  text: {
    noun: string;
    title: string;
    intro: ReactNode;
    linesHint: string;
    sourceQuantity: string;
    openLabel: string;
    fillAll: string;
    over: (open: number) => string;
    dropped: string;
    serverZero: string;
    serverOver: (open: number) => string;
    noOpen: string;
    exceededSummary: string;
    reload: string;
    partiesHint: string;
    step2Title: string;
    step2Description: ReactNode;
    autoParty: string;
    leaveUnknown: string;
  };
}

/** 라인 칸 문제 — 빈칸·0은 '이번엔 안 실음'(문제 아님), 그 밖은 정수·보이는 남은 양 이내. */
function lineProblem(raw: string, open: number, over: (open: number) => string): string | null {
  const value = raw.trim();
  if (value === "" || value === "0") return null;
  if (!/^[1-9][0-9]*$/.test(value) || Number(value) > MAX_QUANTITY) return "1 이상의 정수로 입력해 주세요.";
  if (Number(value) > open) return over(open);
  return null;
}

/** 422 `detail.internal_note`(보이지 않는 글자 등) — 메모 칸 아래 문구. 없으면 null. */
function noteFieldError(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const message = error.detail.internal_note;
  return typeof message === "string" ? message : null;
}

// ── 수출(수주 참조) ─────────────────────────────────────────────────────────

interface SoProps {
  so: SalesOrderDetail;
  onClose: () => void;
  /** 원천 수주가 그 사이 바뀜(409 상태) — 부모가 닫고 다시 불러온다. */
  onReload: () => void;
}

export function ShipmentCreateDialog({ so, onClose, onReload }: SoProps) {
  const flavor: ShipmentFlavor<ShipmentCreateBody, ShipmentPreview> = {
    docNumber: so.doc_number,
    previewPath: `/v1/sales-orders/${so.id}/shipments/preview`,
    createPath: `/v1/sales-orders/${so.id}/shipments`,
    lines: so.lines.map((line) => ({
      id: line.id,
      line_no: line.line_no,
      sku_code: line.sku_code,
      sku_name_ko: line.sku_name_ko,
      quantity: line.quantity,
      open: line.shipment_open_quantity ?? 0,
    })),
    buildBody: (chosen, common) => ({ lines: chosen.map((line) => ({ so_line_id: line.id, quantity: line.quantity })), ...common }),
    lineIdsOf: (body) => body.lines.map((line) => line.so_line_id),
    conflictByLine: openQuantityByLine,
    // 화면의 '남은 잔량'·이 수주의 선적 목록·보드·흐름도 서버 값으로 다시 받는다 — 옛 잔량이 서버 안내와 엇갈리지 않게
    // (실브라우저 관통 발견 + 적대 검토 low ⑥⑩).
    refreshAfterConflict: (client) => refreshAfterQuantityConflict(client, so.id),
    afterCreated: (client) => {
      // 첫 선적이면 수주가 같은 트랜잭션에서 '선적중'이 된다 — 수주·보드·흐름·선적 목록을 다시 불러오게 한다.
      void client.invalidateQueries({ queryKey: salesOrderDetailKey(so.id) });
      void client.invalidateQueries({ queryKey: [...SALES_ORDERS_QUERY_KEY, "list"] });
      void client.invalidateQueries({ queryKey: SHIPMENTS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: DOCUMENT_FLOW_QUERY_KEY });
      void client.invalidateQueries({ queryKey: ORDER_BOARD_QUERY_KEY });
    },
    refreshOnLeave: (client) => {
      void client.invalidateQueries({ queryKey: soShipmentsKey(so.id) });
      void client.invalidateQueries({ queryKey: salesOrderDetailKey(so.id), exact: true });
    },
    previewDocNumber: (data) => data.so_doc_number,
    renderPreview: (data) => <ExportPreview data={data} />,
    text: {
      noun: "수주",
      title: `선적 만들기 — ${so.doc_number}`,
      intro: (
        <>
          통화·환율·결제조건·인코텀즈·품목·단가·바이어는 수주에서 그대로 복사됩니다(다시 입력하지 않습니다). 만들면 <strong>계획</strong>{" "}
          상태로 시작하고, 첫 선적이면 수주가 &lsquo;선적중&rsquo;이 됩니다.
        </>
      ),
      linesHint: "빈칸·0인 라인은 이번 선적에 싣지 않습니다. 남은 수량 안에서 나눠 실을 수 있습니다.",
      sourceQuantity: "수주",
      openLabel: "남은 잔량",
      fillAll: "잔량 전부",
      over: (open) => `남은 수량(${open})을 넘습니다.`,
      dropped: "남은 수량이 없어 이번 선적에서 뺐습니다(서버 재확인 결과).",
      serverZero: "서버 확인: 남은 수량 0 — 이 라인은 비워 주세요(이번 선적에서 빼기).",
      serverOver: (open) => `서버 확인: 남은 수량 ${open} — 수량을 ${open} 이하로 고쳐 주세요.`,
      noOpen: "남은 수량이 없습니다(이미 다른 선적에 모두 배정).",
      exceededSummary: "라인별 남은 수량은 각 수량 칸 아래에 표시했습니다.",
      reload: "수주 다시 불러오기",
      partiesHint: "수하인은 수주 바이어의 영문 이름·주소가 자동으로 복사됩니다(자동 — 여기서 지정하지 않습니다).",
      step2Title: "선적을 만들까요?",
      step2Description: (
        <>
          아래는 서버가 계산한 미리보기입니다(아직 저장되지 않았습니다). &lsquo;생성 확정&rsquo;을 누르면 <strong>계획</strong> 상태의 선적이
          만들어지고 선적번호가 붙습니다.
        </>
      ),
      autoParty: "(자동 — 수주 바이어)",
      leaveUnknown: "확인 없이 닫기(선적 목록에서 확인)",
    },
  };
  return <ReferenceShipmentDialog flavor={flavor} onClose={onClose} onReload={onReload} />;
}

function ExportPreview({ data }: { data: ShipmentPreview }) {
  return (
    <>
      <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <DocField label="거래 상대">{data.counterparty.name}</DocField>
        <DocField label="출발국 → 도착국">
          <span className="cell-nowrap">{countryText(data.origin_country_code)}</span>
          {" → "}
          <span className="cell-nowrap">{countryText(data.dest_country_code)}</span>
        </DocField>
        <DocField label="증빙일">
          <span className="num cell-nowrap">{data.doc_date}</span>
        </DocField>
        <DocField label="통화·고정 환율">
          <span className="num cell-nowrap">
            {data.currency} · {show(data.fx_rate)} (기준일 {show(data.fx_rate_date)})
          </span>
        </DocField>
        <DocField label="결제조건">{paymentTermsText(data.payment_terms)}</DocField>
        <DocField label="인코텀즈">{incotermText(data.incoterm)}</DocField>
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
            {data.lines.map((line) => (
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
                {data.total_text} {data.currency}
              </td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
    </>
  );
}

// ── 수입(발주 참조 — S3-2 PR-5b) ─────────────────────────────────────────────

interface PoProps {
  po: PurchaseOrderDetail;
  onClose: () => void;
  /** 원천 발주가 그 사이 바뀜(409 DOCUMENT_NOT_CONSUMABLE — 취소 등) — 부모가 닫고 다시 불러온다. */
  onReload: () => void;
}

/**
 * 발주 → 수입선적 만들기. 남은 양 = 라인 **배정 가능량**(`assignable_quantity` — PO 잔량 아님), 초과 409 = `EXCEEDS_ASSIGNABLE`.
 * 송하인 = 발주 공급사 자동·수하인 = 자사(본문으로 보내지 않는다). 발주 상태·version·잔량은 바뀌지 않는다(PR-5a).
 * ★ 원천 발주 응답의 원가 키(단가·금액·통화·환율)는 대화상자로 옮기지 않는다 — 라인 번호·SKU·수량·배정 가능량만.
 */
export function ImportShipmentCreateDialog({ po, onClose, onReload }: PoProps) {
  const flavor: ShipmentFlavor<ImportShipmentCreateBody, ImportShipmentPreview> = {
    docNumber: po.doc_number,
    previewPath: `/v1/purchase-orders/${po.id}/shipments/preview`,
    createPath: `/v1/purchase-orders/${po.id}/shipments`,
    lines: po.lines.map((line) => ({
      id: line.id,
      line_no: line.line_no,
      sku_code: line.sku_code,
      sku_name_ko: line.sku_name_ko,
      quantity: line.quantity,
      // 모르는 값(재생 본문 null — R-5a-6)은 0 — 칸을 닫는다(fail-closed, 서버가 정본).
      open: typeof line.assignable_quantity === "number" ? line.assignable_quantity : 0,
    })),
    buildBody: (chosen, common) => ({ lines: chosen.map((line) => ({ po_line_id: line.id, quantity: line.quantity })), ...common }),
    lineIdsOf: (body) => body.lines.map((line) => line.po_line_id),
    conflictByLine: assignableByLine,
    // 발주 상세(라인 배정 가능량·입고예정)·이 발주의 수입선적 목록을 서버 값으로 다시 받는다 — 칸 옆 옛 배정 가능량이 서버 안내와 엇갈리지 않게.
    refreshAfterConflict: (client) => refreshAfterAssignableConflict(client, po.id),
    afterCreated: (client) => {
      // 발주 상태·version은 그대로다(PR-5a) — 라인 배정 가능량·입고예정과 수입선적 목록만 바뀐다.
      void client.invalidateQueries({ queryKey: purchaseOrderDetailKey(po.id) });
      void client.invalidateQueries({ queryKey: [...PURCHASE_ORDERS_QUERY_KEY, "list"] });
      void client.invalidateQueries({ queryKey: SHIPMENTS_QUERY_KEY });
    },
    refreshOnLeave: (client) => {
      void client.invalidateQueries({ queryKey: poShipmentsKey(po.id) });
      void client.invalidateQueries({ queryKey: purchaseOrderDetailKey(po.id), exact: true });
    },
    previewDocNumber: (data) => data.po_doc_number,
    renderPreview: (data) => <ImportPreview data={data} />,
    text: {
      noun: "발주",
      title: `수입선적 만들기 — ${po.doc_number}`,
      intro: (
        <>
          결제조건·인코텀즈·품목·공급사는 발주에서 그대로 복사됩니다(다시 입력하지 않습니다). <strong>단가·금액·통화·환율은 복사하지
          않습니다</strong>(원가 비복사). 만들면 <strong>계획</strong> 상태로 시작하고, 발주의 상태·수량은 바뀌지 않습니다.
        </>
      ),
      linesHint: "빈칸·0인 라인은 이번 선적에 싣지 않습니다. 배정 가능량 안에서 여러 수입선적으로 나눠 실을 수 있습니다.",
      sourceQuantity: "발주",
      openLabel: "배정 가능",
      fillAll: "배정 가능 전부",
      over: (open) => `배정 가능량(${open})을 넘습니다.`,
      dropped: "배정 가능량이 없어 이번 선적에서 뺐습니다(서버 재확인 결과).",
      serverZero: "서버 확인: 배정 가능량 0 — 이 라인은 비워 주세요(이번 선적에서 빼기).",
      serverOver: (open) => `서버 확인: 배정 가능량 ${open} 초과 — 수량을 ${open} 이하로 고쳐 주세요.`,
      noOpen: "배정 가능량이 없습니다(이미 다른 수입선적에 모두 배정).",
      exceededSummary: "라인별 배정 가능량은 각 수량 칸 아래에 표시했습니다.",
      reload: "발주 다시 불러오기",
      partiesHint:
        "송하인은 발주 공급사의 영문 이름·주소가 자동으로 복사되고, 수하인은 자사입니다(자동 — 여기서 지정하지 않습니다).",
      step2Title: "수입선적을 만들까요?",
      step2Description: (
        <>
          아래는 서버가 계산한 미리보기입니다(아직 저장되지 않았습니다). &lsquo;생성 확정&rsquo;을 누르면 <strong>계획</strong> 상태의
          수입선적이 만들어지고 선적번호가 붙습니다. 발주의 상태·수량은 바뀌지 않습니다.
        </>
      ),
      autoParty: "(자동 — 발주 공급사)",
      leaveUnknown: "확인 없이 닫기(수입선적 목록에서 확인)",
    },
  };
  return <ReferenceShipmentDialog flavor={flavor} onClose={onClose} onReload={onReload} />;
}

/** 수입 미리보기 — **금액·통화 칸 0**(응답에 키가 없다): 거래 상대·국가·증빙일·결제조건·인코텀즈 + 라인 수량·배정 가능량 전/후·DG. */
function ImportPreview({ data }: { data: ImportShipmentPreview }) {
  return (
    <>
      <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <DocField label="공급사">{data.counterparty.name}</DocField>
        <DocField label="출발국 → 도착국">
          <span className="cell-nowrap">{countryText(data.origin_country_code)}</span>
          {" → "}
          <span className="cell-nowrap">{countryText(data.dest_country_code)}</span>
        </DocField>
        <DocField label="증빙일">
          <span className="num cell-nowrap">{data.doc_date}</span>
        </DocField>
        <DocField label="결제조건">{paymentTermsText(data.payment_terms)}</DocField>
        <DocField label="인코텀즈">{incotermText(data.incoterm)}</DocField>
      </dl>
      <div className="mt-3 overflow-x-auto rounded border border-gray-200">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-gray-600">
            <tr>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">번호</th>
              <th scope="col" className="cell-nowrap px-3 py-2">SKU</th>
              <th scope="col" className="cell-nowrap min-w-32 px-3 py-2">품명</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">이번 수량</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">배정 전 배정 가능</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">배정 후 배정 가능</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">DG</th>
            </tr>
          </thead>
          <tbody>
            {data.lines.map((line) => (
              <tr key={line.po_line_id} className="border-t border-gray-100">
                <td className="num cell-nowrap px-3 py-2">{line.line_no}</td>
                <td className="cell-nowrap px-3 py-2">{line.sku.code}</td>
                <td className="break-keep px-3 py-2">{line.sku.name_ko}</td>
                <td className="num cell-nowrap px-3 py-2">{line.quantity}</td>
                <td className="num cell-nowrap px-3 py-2">{line.assignable_before}</td>
                <td className="num cell-nowrap px-3 py-2">{line.remaining_after}</td>
                <td className="px-3 py-2 text-center">
                  <DgBadge dg={line.dg} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 break-keep text-xs text-gray-500">수입선적은 단가·금액·통화를 복사하지 않습니다(원가 비복사).</p>
    </>
  );
}

// ── 흐름 코어(수출·수입 공용) ──────────────────────────────────────────────────

function ReferenceShipmentDialog<B extends CommonBody, P extends PreviewWithParties>({
  flavor,
  onClose,
  onReload,
}: {
  flavor: ShipmentFlavor<B, P>;
  onClose: () => void;
  onReload: () => void;
}) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const titleId = useId();
  const noteId = useId();
  const { text } = flavor;

  const [qty, setQty] = useState<Record<number, string>>(() => Object.fromEntries(flavor.lines.map((line) => [line.id, ""])));
  const [origin, setOrigin] = useState("");
  const [dest, setDest] = useState("");
  const [parties, setParties] = useState<Parties>({ NOTIFY: null, FORWARDER: null, CUSTOMS_BROKER: null });
  const [note, setNote] = useState("");
  const [triedPreview, setTriedPreview] = useState(false);
  // 서버 409의 라인별 남은 양 — 1단 칸 아래에 붙인다(입력을 고치면 그 칸의 안내만 지운다).
  const [serverOpen, setServerOpen] = useState<Map<number, number>>(new Map());
  // 서버 422의 라인별 칸 오류(`lines[i]…` — 예: 이 원천의 라인이 아님)·메모 칸 오류 — 고치면 그 칸의 안내만 지운다.
  const [serverField, setServerField] = useState<Map<number, string>>(new Map());
  const [serverNote, setServerNote] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ data: P; body: B } | null>(null);
  // 재조회로 남은 양이 0이 되어 자동으로 비운 라인(막다른 길 방지 — 사용자가 다시 손대면 지운다).
  const [dropped, setDropped] = useState<Set<number>>(new Set());
  // 늦게 온 미리보기 응답을 버린 사실(입력이 바뀌었다) — 다시 미리보기를 누르게 안내한다.
  const [discardedPreview, setDiscardedPreview] = useState(false);
  const [focusRequest, setFocusRequest] = useState<{ target: "step1" | "step2" | "problem"; seq: number } | null>(null);

  const boxRef = useRef<HTMLDivElement | null>(null);
  const step1Heading = useRef<HTMLHeadingElement | null>(null);
  const step2Heading = useRef<HTMLHeadingElement | null>(null);
  const focusSeq = useRef(0);
  const previewLock = useRef(false);
  const createLock = useRef(false);
  // 지금 화면 입력으로 만든 본문(직렬화) — 늦게 온 미리보기 응답이 이 본문의 것인지 대조한다.
  const currentBody = useRef("");
  const [keys] = useState(() => createKeyKeeper()); // 대화상자 1회 열림 = 키 1개

  function requestFocus(target: "step1" | "step2" | "problem") {
    focusSeq.current += 1;
    setFocusRequest({ target, seq: focusSeq.current });
  }

  function afterError(error: unknown, body: B) {
    const open = flavor.conflictByLine(error);
    if (open.size > 0) {
      setServerOpen(open);
      setPreview(null); // 1단으로 돌아가 칸별 남은 양을 보인다
      requestFocus("problem");
      // 화면의 남은 양·이 원천의 선적 목록 등을 서버 값으로 다시 받는다. 입력값은 라인 id로 묶여 그대로 남고, 0이 된 라인만 아래 효과가 비운다.
      flavor.refreshAfterConflict(client);
      return;
    }
    // 422 칸 오류 — 보낸 본문의 라인 순서로 라인에 붙이고 1단 그 칸으로 돌아간다(2단 요약 문구만으로는 고칠 칸을 모른다).
    const fields = lineFieldErrors(error, flavor.lineIdsOf(body));
    const noteError = noteFieldError(error);
    if (fields.size > 0 || noteError !== null) {
      setServerField(fields);
      setServerNote(noteError);
      setPreview(null);
      requestFocus("problem");
    }
  }

  const previewMutation = useMutation({
    // 동기 잠금은 요청 자체의 finally에서 푼다 — 관찰자가 reset돼도 잠금이 남지 않는다(적대 검토 med ③).
    mutationFn: async (body: B) => {
      try {
        return await apiFetch<P>(flavor.previewPath, { method: "POST", body });
      } finally {
        previewLock.current = false;
      }
    },
    onSuccess: (data, body) => {
      // 늦게 온 응답 — 보낸 뒤 입력이 바뀌었으면 옛 본문으로 2단을 열지 않는다(확정이 화면과 다른 것을 만들지 않게).
      if (JSON.stringify(body) !== currentBody.current) {
        setDiscardedPreview(true);
        return;
      }
      setDiscardedPreview(false);
      setPreview({ data, body });
      requestFocus("step2");
    },
    onError: (error, body) => afterError(error, body),
  });

  const createMutation = useMutation({
    mutationFn: async (input: { key: string; body: B }) => {
      try {
        return await apiFetch<ShipmentDetail>(flavor.createPath, {
          method: "POST",
          idempotencyKey: input.key,
          body: input.body,
        });
      } finally {
        createLock.current = false;
      }
    },
    onSuccess: (created) => {
      flavor.afterCreated(client);
      void navigate(`/shipments/${created.id}`);
    },
    onError: (error, input) => afterError(error, input.body),
  });

  const busy = previewMutation.isPending || createMutation.isPending;
  // 결과를 모르는 생성 실패(0·5xx) — 같은 키로 다시 확정해 결과를 확인하기 전에는 '뒤로'·Esc로 떠나지 않게(적대 검토 low ⑧).
  const unknownResult = preview !== null && isResultUnknown(createMutation.error);
  useDialogBehavior(boxRef, () => {
    if (!busy && !unknownResult) onClose();
  });

  /** 입력이 바뀌면 이전 시도의 오류를 버린다 — 진행 중인 요청은 건드리지 않는다(reset이 늦은 응답의 처리를 끊지 않게). */
  function touched() {
    if (!previewMutation.isPending) previewMutation.reset();
    if (!createMutation.isPending) createMutation.reset();
    setDiscardedPreview(false);
  }

  function clearLine(lineId: number) {
    setServerOpen((prev) => {
      const next = new Map(prev);
      next.delete(lineId);
      return next;
    });
    setServerField((prev) => {
      const next = new Map(prev);
      next.delete(lineId);
      return next;
    });
  }

  // 재조회로 남은 양이 0 이하가 된 라인에 입력값이 남으면 칸이 비활성인 채 미리보기가 영원히 막힌다 — 자동으로 비우고 알린다(적대 검토 med ②).
  const openSignature = flavor.lines.map((line) => `${line.id}:${line.open}`).join(",");
  useEffect(() => {
    const emptied = flavor.lines.filter((line) => line.open <= 0 && (qty[line.id] ?? "").trim() !== "");
    if (emptied.length === 0) return;
    setQty((prev) => ({ ...prev, ...Object.fromEntries(emptied.map((line) => [line.id, ""])) }));
    setDropped((prev) => new Set([...prev, ...emptied.map((line) => line.id)]));
    // 409 복귀 포커스가 그 칸에 있었다면 칸이 비활성이 되며 포커스가 문서로 빠진다(실브라우저 재확인 발견) — 다음 문제 칸·1단 제목으로 다시 옮긴다.
    requestFocus("problem");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openSignature, qty]);

  // 단계가 바뀌면(1단↔2단·409 복귀) 대화상자를 맨 위로 올리고 단계 제목(409 복귀는 첫 문제 칸)에 포커스(적대 검토 low ⑪).
  useEffect(() => {
    if (focusRequest === null) return;
    const box = boxRef.current;
    if (box) box.scrollTop = 0;
    if (focusRequest.target === "problem") {
      const field = box?.querySelector<HTMLElement>('input[aria-invalid="true"]:not(:disabled)') ?? null;
      (field ?? step1Heading.current)?.focus();
    } else {
      (focusRequest.target === "step2" ? step2Heading : step1Heading).current?.focus();
    }
  }, [focusRequest]);

  const lineProblems = new Map<number, string>();
  for (const line of flavor.lines) {
    const problem = lineProblem(qty[line.id] ?? "", line.open, text.over);
    if (problem !== null) lineProblems.set(line.id, problem);
  }
  const chosenLines = flavor.lines.filter((line) => /^[1-9][0-9]*$/.test((qty[line.id] ?? "").trim()));
  const originProblem = countryProblem(origin);
  const destProblem = countryProblem(dest);
  const canPreview = chosenLines.length > 0 && lineProblems.size === 0 && originProblem === null && destProblem === null;

  function buildBody(): B {
    const common: CommonBody = { origin_country_code: origin, dest_country_code: dest };
    const chosenParties = SELECTABLE_PARTY_ROLES.flatMap((role) => {
      const partner = parties[role];
      return partner === null ? [] : [{ role, partner_id: partner.id }];
    });
    if (chosenParties.length > 0) common.parties = chosenParties;
    if (note.trim() !== "") common.internal_note = note.trim();
    return flavor.buildBody(
      chosenLines.map((line) => ({ id: line.id, quantity: Number((qty[line.id] ?? "").trim()) })),
      common,
    );
  }
  currentBody.current = JSON.stringify(buildBody());

  function submitPreview() {
    setTriedPreview(true);
    if (!canPreview || previewLock.current) return;
    previewLock.current = true; // 동기 잠금(해제는 요청 finally)
    setServerOpen(new Map());
    setServerField(new Map());
    setServerNote(null);
    setDropped(new Set());
    setDiscardedPreview(false);
    previewMutation.mutate(buildBody());
  }

  function confirmCreate() {
    if (preview === null || createLock.current) return;
    createLock.current = true; // 해제는 요청 finally
    // ★ 미리보기 때의 본문 그대로 — 같은 본문 재확정은 같은 키(결과를 모르는 실패 뒤 중복 선적 방지), 다르면 새 키.
    const key = keys.keyFor(JSON.stringify(preview.body));
    createMutation.mutate({ key, body: preview.body });
  }

  const activeError = createMutation.error ?? previewMutation.error;
  const exceeded = flavor.conflictByLine(activeError).size > 0;

  // 409의 상단 요약은 서버 문구 그대로(design-D D4) — 원인을 단정하는 고정 문구를 쓰지 않는다. 칸별 남은 양은 각 칸 아래.
  const errorBlock = activeError !== null && (
    <div role="alert" className="mt-3 text-sm text-signal-red">
      <p className="break-keep">{errorMessage(activeError, "선적을 만들지 못했습니다.", text.noun)}</p>
      {exceeded && <p className="mt-1 break-keep">{text.exceededSummary}</p>}
      {unknownResult && (
        <p className="mt-1 break-keep font-medium">
          응답을 받지 못해 생성 여부를 알 수 없습니다. &lsquo;생성 확정&rsquo;을 한 번 더 누르면 같은 요청(같은 키)으로 결과를 확인합니다 — 새로
          만들지 않습니다.
        </p>
      )}
      <PartnerFixHint error={activeError} />
      {activeError instanceof ApiError && activeError.code === LOCK_BUSY_CODE && (
        <p className="mt-1 break-keep">잠시 후 같은 버튼을 다시 눌러 주세요(같은 요청으로 다시 보냅니다).</p>
      )}
      {(isVersionConflict(activeError) ||
        (activeError instanceof ApiError && activeError.code === DOCUMENT_NOT_CONSUMABLE_CODE)) && (
        <button type="button" onClick={onReload} className="cell-nowrap mt-2 rounded border border-signal-red px-3 py-1">
          {text.reload}
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
            <h2 id={titleId} ref={step1Heading} tabIndex={-1} className="text-lg font-bold">
              {text.title}
            </h2>
            <p className="mt-1 break-keep text-sm text-gray-500">{text.intro}</p>

            {/* 미리보기 대기 중에는 1단 입력을 잠근다 — 보낸 본문과 화면이 갈라지지 않게(적대 검토 med ③). */}
            <fieldset disabled={previewMutation.isPending} className="contents">
            <h3 className="mt-4 text-sm font-semibold">이번 선적 수량</h3>
            <p className="break-keep text-xs text-gray-500">{text.linesHint}</p>
            <ul className="mt-2 grid gap-2" aria-label="선적할 라인">
              {flavor.lines.map((line) => {
                const open = line.open;
                const problem = lineProblems.get(line.id) ?? null;
                const emptyNow = (qty[line.id] ?? "").trim() === "";
                const wasDropped = dropped.has(line.id) && emptyNow;
                const server = wasDropped ? undefined : serverOpen.get(line.id);
                const fieldError = serverField.get(line.id);
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
                        {text.sourceQuantity} <span className="num">{line.quantity}</span>
                      </span>
                      <span className="cell-nowrap text-gray-600">
                        {text.openLabel} <span className="num font-medium">{open}</span>
                      </span>
                      <label className="flex flex-col gap-1">
                        <span className="cell-nowrap text-gray-600">이번 선적 수량</span>
                        <input
                          inputMode="numeric"
                          aria-label={`라인 ${line.line_no} 이번 선적 수량`}
                          aria-invalid={problem !== null || server !== undefined || fieldError !== undefined}
                          aria-describedby={hintId}
                          value={qty[line.id] ?? ""}
                          disabled={open <= 0}
                          onChange={(event) => {
                            touched();
                            clearLine(line.id);
                            setDropped((prev) => {
                              const next = new Set(prev);
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
                        aria-label={`라인 ${line.line_no} ${text.fillAll}`}
                        onClick={() => {
                          touched();
                          clearLine(line.id);
                          setQty((prev) => ({ ...prev, [line.id]: String(open) }));
                        }}
                        className="cell-nowrap rounded border border-gray-300 px-3 py-1 disabled:opacity-50"
                      >
                        {text.fillAll}
                      </button>
                      <AvailabilityBadge />
                    </div>
                    <p id={hintId} className="mt-1 break-keep text-xs">
                      {wasDropped ? (
                        <span role="status" className="text-gray-700">
                          {text.dropped}
                        </span>
                      ) : server !== undefined ? (
                        <span role="alert" className="text-signal-red">
                          {server <= 0 ? text.serverZero : text.serverOver(server)}
                        </span>
                      ) : fieldError !== undefined ? (
                        <span role="alert" className="text-signal-red">
                          서버 확인: {fieldError}
                        </span>
                      ) : problem !== null ? (
                        <span className="text-signal-red">{problem}</span>
                      ) : open <= 0 ? (
                        <span className="text-gray-500">{text.noOpen}</span>
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
              <p className="break-keep text-xs text-gray-500">{text.partiesHint}</p>
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
                aria-invalid={serverNote !== null}
                aria-describedby={serverNote !== null ? noteId : undefined}
                onChange={(event) => {
                  touched();
                  setServerNote(null);
                  setNote(event.target.value);
                }}
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            {serverNote !== null && (
              <p id={noteId} role="alert" className="mt-1 break-keep text-xs text-signal-red">
                서버 확인: {serverNote}
              </p>
            )}

            </fieldset>

            {errorBlock}
            {discardedPreview && (
              <p role="status" className="mt-3 break-keep text-sm text-gray-700">
                미리보기를 받는 사이 입력이 바뀌어 그 결과를 버렸습니다. &lsquo;다음: 미리보기&rsquo;를 다시 눌러 주세요.
              </p>
            )}

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
            <h2 id={titleId} ref={step2Heading} tabIndex={-1} className="text-lg font-bold">
              {text.step2Title} — {flavor.previewDocNumber(preview.data)}
            </h2>
            <p className="mt-1 break-keep text-sm text-gray-500">{text.step2Description}</p>
            {flavor.renderPreview(preview.data)}
            <h3 className="mt-4 text-sm font-semibold">당사자</h3>
            <PreviewParties parties={preview.data.parties} autoLabel={text.autoParty} />

            {errorBlock}

            <div className="mt-5 flex justify-end gap-2">
              {unknownResult && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    // 결과 확인 없이 떠난다 — 이 원천의 선적 목록을 다시 받아 생성 여부를 화면에서 확인하게 한다.
                    flavor.refreshOnLeave(client);
                    onClose();
                  }}
                  className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm disabled:opacity-50"
                >
                  {text.leaveUnknown}
                </button>
              )}
              <button
                type="button"
                disabled={busy || unknownResult}
                title={unknownResult ? "생성 결과를 확인하기 전에는 뒤로 갈 수 없습니다 — '생성 확정'을 다시 눌러 결과를 확인하세요." : undefined}
                onClick={() => {
                  createMutation.reset();
                  setPreview(null);
                  requestFocus("step1");
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

/** 미리보기 당사자 — 두 미리보기 응답 모두 `parties[{role, partner_id, name_en, auto}]`. */
function PreviewParties({ parties, autoLabel }: { parties: ShipmentPreviewParty[]; autoLabel: string }) {
  return (
    <ul className="mt-1 grid gap-1 text-sm">
      {parties.map((party) => (
        <li key={`${party.role}-${party.partner_id}`} className="break-keep">
          <span className="cell-nowrap font-medium">{partyRoleLabel(party.role)}</span> {party.name_en}
          {party.auto && <span className="ml-2 cell-nowrap text-xs text-gray-500">{autoLabel}</span>}
        </li>
      ))}
    </ul>
  );
}
