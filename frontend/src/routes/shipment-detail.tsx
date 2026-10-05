// 선적 상세 `/shipments/:shipmentId` (S3-2 PR-3b — design-D D6·D8·D9·D10·D13 / PROGRESS 'S3-2 PR-3a' 인계 계약).
//
// 섹션(세로 쌓기): 선적 정보(헤더·DG 요약) → 메모·담당·국가 → 마일스톤(휴일 요약·타임라인) → 라인(가용재고 '미산정' 자리) → 당사자 →
// 통관 기록 → 마일스톤 변경 이력(롤오버·통보) → 문서 흐름 → 상태 이력. (PR-4b — design-D D6·D7: 마일스톤·통관·롤오버·통보)
// - 마일스톤 쓰기는 헤더 version을 바꾸지 않는다 — 응답의 `board`로 상세 캐시의 `milestones` 칸만 바꾼다(기준 version 점프 0,
//   진행 중이던 상세 재조회는 취소해 늦은 응답이 새 보드를 덮지 못하게).
// 규칙(서버가 정본, 화면은 편의 — §18.1):
// - 버튼은 서버가 준 `allowed_actions`로만 보인다(역할·상태 규칙을 화면에 복제하지 않는다).
// - 쓰기는 화면이 본 기준 version(baseVersion)을 싣는다 — 서버 version이 앞서가면 '다른 곳에서 수정' 배너 + 불러오기(서버 409가 덮어쓰기를 막는다).
// - 확인 대화상자·추가 폼 1회 = 멱등 키 1개(같은 본문 재시도 = 같은 키, 본문이 바뀌면 새 키), 더블클릭은 동기 잠금(ref).
// - 금액은 서버 문자열, 날짜(`doc_date`·`fx_rate_date`)는 문자열 그대로, 시각만 KST(`toKstDisplay`).
// - (S3-2 PR-5b) 응답은 `shipment_kind` 판별자 합집합 — 수입선적엔 통화·환율·합계·라인 단가/금액 키가 없어 그 칸들을 **그리지 않는다**
//   (금액 칸 0 — 원가 열람 역할도 같다, R-5a-4). 수입 라인 원천 = 발주 라인(배정 가능량), 라인 추가 후보 = 발주 상세의 라인.
//   수입의 동결 전이(RELEASE_ORDER)는 '선적 확정'으로 부른다(R-5a-8 — 화면 문구만).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { ConfirmDialog } from "../components/confirm-dialog";
import { DocumentFlowPanel } from "../components/document-flow-panel";
import { MilestoneChangesSection } from "../components/milestone-changes";
import { CUSTOMS_HEADING_ID, CUSTOMS_SECTION_ID, CustomsSection } from "../components/milestone-customs";
import { MilestoneValueDialog } from "../components/milestone-dialogs";
import { HolidaySummaryBanner, MilestoneTimeline, type MilestoneEditMode } from "../components/milestone-timeline";
import { DocField, EMPTY, incotermText, paymentTermsText, show } from "../components/proforma-facts";
import { SearchSelect } from "../components/search-select";
import { AvailabilityBadge, CountryInput, DgBadge, PartnerFixHint, countryProblem } from "../components/shipment-parts";
import { StatusTimeline } from "../components/status-timeline";
import { ApiError, apiFetch } from "../lib/api";
import { errorMessage, isVersionConflict } from "../lib/api-errors";
import { toKstDisplay } from "../lib/datetime";
import { purchaseOrderStatusLabel, salesOrderStatusLabel } from "../lib/doc-status";
import {
  cancelBlockers,
  derivedChanges,
  milestoneChangesKey,
  milestoneTypeLabel,
  needsBoardReload,
  type MilestoneBoard,
  type MilestoneRow,
} from "../lib/milestone";
import { ORDER_BOARD_QUERY_KEY } from "../lib/order-board";
import { usePagedQuery } from "../lib/paging";
import { DOCUMENT_FLOW_QUERY_KEY, SALES_ORDERS_QUERY_KEY, salesOrderDetailKey, type SalesOrderDetail } from "../lib/sales-order";
import { purchaseOrderDetailKey, type PurchaseOrderDetail } from "../lib/purchase-order";
import { hasRole, useSession } from "../lib/session";
import {
  PARTY_PARTNER_TYPE,
  SELECTABLE_PARTY_ROLES,
  SHIPMENTS_QUERY_KEY,
  can,
  countryText,
  createKeyKeeper,
  isExportShipment,
  partyRoleLabel,
  poShipmentsKey,
  quantityConflictByLine,
  refreshAfterAssignableConflict,
  refreshAfterQuantityConflict,
  releaseActionLabel,
  shipmentDetailKey,
  shipmentKindLabel,
  shipmentStatusText,
  type SelectablePartyRole,
  type ShipmentDetail,
  type ShipmentLine,
  type ShipmentParty,
} from "../lib/shipment";
import type { Partner } from "./partners";
import { ShipmentStatusBadge } from "./shipments";

const NOUN = "선적";
const inputClass = "rounded border border-gray-300 px-3 py-2 text-sm";
const MAX_QUANTITY = 99_999_999;

const isPositiveInt = (value: string): boolean => /^[1-9][0-9]*$/.test(value.trim()) && Number(value) <= MAX_QUANTITY;

interface UserLookup {
  id: number;
  display_name: string;
}

/** 원천 수주 id — 수출선적만(수입선적의 원천은 발주라 수주 재조회 대상이 없다). */
const soIdOf = (shipment: ShipmentDetail): number | null => (shipment.source.kind === "SALES_ORDER" ? shipment.source.id : null);
/** 원천 발주 id — 수입선적만(배정 가능량·입고예정을 보여 주는 발주 상세 재조회 대상). */
const poIdOf = (shipment: ShipmentDetail): number | null => (shipment.source.kind === "PURCHASE_ORDER" ? shipment.source.id : null);
/** 라인의 원천 라인 id — 수출 = SO 라인, 수입 = PO 라인. */
const sourceLineIdOf = (line: ShipmentLine): number | null => ("po_line_id" in line ? line.po_line_id : line.so_line_id);

/** 수량 초과 409 뒤 재조회 — 수출은 수주·보드·흐름, 수입은 발주 상세·이 발주의 수입선적 목록. */
function refreshAfterConflict(client: ReturnType<typeof useQueryClient>, shipment: ShipmentDetail) {
  const poId = poIdOf(shipment);
  if (poId !== null) refreshAfterAssignableConflict(client, poId, shipment.id);
  else refreshAfterQuantityConflict(client, soIdOf(shipment), shipment.id);
}

/** 선적 오류 문구 — 서버 한국어 message 그대로(낙관 잠금은 '선적' 안내문). */
const shipmentError = (error: unknown, fallback = "요청을 처리하지 못했습니다."): string => errorMessage(error, fallback, NOUN);

const MILESTONES_HEADING_ID = "shipment-milestones-title";

/** 대화상자가 닫힌 뒤(포커스 복귀가 끝난 뒤) 그 섹션 제목으로 옮긴다 — 취소 409 '먼저 할 일'로 이어 주기. */
function focusSection(headingId: string) {
  setTimeout(() => {
    const heading = document.getElementById(headingId);
    heading?.scrollIntoView?.({ block: "start" });
    heading?.focus();
  }, 0);
}

/** 선적이 바뀌면 화면 상태 전체를 새로 시작한다 — 이전 선적의 기준 version·폼이 남지 않게. */
export function ShipmentDetailPage() {
  const params = useParams();
  return <ShipmentDetailView key={params.shipmentId} />;
}

type Action = "release" | "cancel";

function ShipmentDetailView() {
  const params = useParams();
  const id = Number(params.shipmentId);
  const client = useQueryClient();
  const detailKey = shipmentDetailKey(id);
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: () => apiFetch<ShipmentDetail>(`/v1/shipments/${id}`),
    enabled: Number.isInteger(id) && id > 0,
    // 쓰기 화면은 항상 서버의 최신 version을 기준으로 한다.
    staleTime: 0,
  });

  const [action, setAction] = useState<Action | null>(null);
  const [notice, setNotice] = useState<unknown>(null);
  const [resetToken, setResetToken] = useState(0);
  // ★ 화면이 "마지막으로 본/내가 쓴" version — 창 포커스 재조회로 서버 version이 앞서가도 쓰기는 이 값으로 보낸다.
  const [baseVersion, setBaseVersion] = useState<number | null>(null);
  const actionLock = useRef(false);
  const [actionKeys, setActionKeys] = useState(() => createKeyKeeper());
  // 마일스톤 대화상자 — 연 순간의 행(기준 version 고정, 뒤에서 보드가 새로 와도 점프하지 않는다).
  const [editing, setEditing] = useState<{ mode: MilestoneEditMode; row: MilestoneRow } | null>(null);
  const [announcements, setAnnouncements] = useState<string[]>([]);
  const draftLock = useRef(false);
  const [draftKeys, setDraftKeys] = useState(() => createKeyKeeper());

  const loadedVersion = detail.data?.version;
  useEffect(() => {
    if (baseVersion === null && loadedVersion !== undefined) setBaseVersion(loadedVersion);
  }, [baseVersion, loadedVersion]);

  /**
   * 선적 쓰기는 원천 수주의 잔량·상태(선적중 수렴)를 바꾼다 — 그 화면들과 보드·흐름도 최신으로. 수입선적은 원천 발주의 라인
   * 배정 가능량·입고예정을 바꾼다(발주 상태·version은 불변 — PR-5a) — 발주 상세·이 발주의 수입선적 목록을 다시 받는다.
   */
  function invalidateRelated(next: ShipmentDetail) {
    const soId = soIdOf(next);
    const poId = poIdOf(next);
    void client.invalidateQueries({ queryKey: SHIPMENTS_QUERY_KEY });
    if (poId !== null) {
      void client.invalidateQueries({ queryKey: purchaseOrderDetailKey(poId) });
      void client.invalidateQueries({ queryKey: poShipmentsKey(poId) });
      return;
    }
    void client.invalidateQueries({ queryKey: DOCUMENT_FLOW_QUERY_KEY });
    void client.invalidateQueries({ queryKey: ORDER_BOARD_QUERY_KEY });
    if (soId !== null) void client.invalidateQueries({ queryKey: salesOrderDetailKey(soId) });
    void client.invalidateQueries({ queryKey: [...SALES_ORDERS_QUERY_KEY, "list"] });
  }

  /**
   * 쓰기 응답 반영. ★ 기준 version은 **헤더 쓰기**(메모·국가·라인·출고지시·취소 — 서버가 base 일치를 검사하고 +1)의 응답으로만 옮긴다.
   * 당사자 추가·제외는 헤더 version을 검사·증가하지 않고 서버 최신 version을 그대로 돌려준다 — 그 값으로 기준을 옮기면 화면이 낡았을 때
   * 메모·국가 폼이 409 없이 남의 수정을 덮는다(적대 검토 med ④). 그때는 기준을 두고 '다른 곳에서 수정' 배너가 뜨게 한다.
   */
  function afterWrite(next: ShipmentDetail, headerWrite = true) {
    client.setQueryData(detailKey, next);
    if (headerWrite) setBaseVersion(next.version);
    setNotice(null);
    invalidateRelated(next);
  }

  /**
   * 마일스톤 쓰기 응답의 보드 반영 — 상세 캐시의 `milestones`만 바꾼다(헤더 version·폼 기준 무변경). 진행 중 재조회는 취소(늦은 응답 폐기),
   * 파생값이 바뀌었으면 안내문(aria-live), 목록 ETD/ETA·변경 이력은 다시 받는다.
   */
  function applyBoard(board: MilestoneBoard) {
    void client.cancelQueries({ queryKey: detailKey, exact: true });
    const previous = client.getQueryData<ShipmentDetail>(detailKey);
    if (previous !== undefined) client.setQueryData(detailKey, { ...previous, milestones: board });
    setAnnouncements(derivedChanges(previous?.milestones, board));
    void client.invalidateQueries({ queryKey: [...SHIPMENTS_QUERY_KEY, "list"] });
    void client.invalidateQueries({ queryKey: milestoneChangesKey("SHIPMENT", id) });
  }

  /** 통관 기록·통보가 바뀌면 보드(신고수리·미통보 수)·통관 요약이 서버에서 다시 계산된다 — 상세를 다시 받는다(기준 version 무변경). */
  function refreshBoard() {
    void client.invalidateQueries({ queryKey: detailKey, exact: true });
    void client.invalidateQueries({ queryKey: [...SHIPMENTS_QUERY_KEY, "list"] });
    void client.invalidateQueries({ queryKey: milestoneChangesKey("SHIPMENT", id) });
  }

  const draft = useMutation({
    mutationFn: async (key: string) => {
      try {
        return await apiFetch<MilestoneBoard>(`/v1/shipments/${id}/milestones/plan-draft`, {
          method: "POST",
          idempotencyKey: key,
          body: {},
        });
      } finally {
        draftLock.current = false;
      }
    },
    onSuccess: (board) => {
      setDraftKeys(createKeyKeeper()); // 다음 초안은 새 요청(같은 키 재시도는 결과를 모르는 실패 때만)
      setNotice(null);
      applyBoard(board);
    },
    onError: (error) => setNotice(error),
  });

  function requestDraft() {
    if (draftLock.current) return;
    draftLock.current = true;
    draft.mutate(draftKeys.keyFor("{}"));
  }

  function reload() {
    setNotice(null);
    setAction(null);
    setEditing(null);
    transition.reset();
    void detail.refetch().then((result) => {
      if (result.data) setBaseVersion(result.data.version);
      setResetToken((value) => value + 1);
    });
    void client.invalidateQueries({ queryKey: DOCUMENT_FLOW_QUERY_KEY });
  }

  const transition = useMutation({
    // 동기 잠금 해제는 요청 자체의 finally에서 — 관찰자(reset·닫기)가 바뀌어도 잠금이 남지 않는다(적대 검토 low ⑨).
    mutationFn: async (input: { kind: Action; key: string; body: Record<string, unknown> }) => {
      try {
        return await apiFetch<ShipmentDetail>(
          input.kind === "release" ? `/v1/shipments/${id}/release-order` : `/v1/shipments/${id}/transitions`,
          { method: "POST", idempotencyKey: input.key, body: input.body },
        );
      } finally {
        actionLock.current = false;
      }
    },
    onSuccess: (next) => {
      setAction(null);
      afterWrite(next);
      setResetToken((value) => value + 1);
    },
  });

  function openAction(next: Action) {
    setActionKeys(createKeyKeeper()); // 대화상자 1회 열림 = 키 1개
    transition.reset();
    setAction(next);
  }

  function submitAction(kind: Action, body: Record<string, unknown>) {
    if (actionLock.current) return; // 동기 잠금 — isPending은 한 틱 늦게 켜진다
    actionLock.current = true;
    const key = actionKeys.keyFor(JSON.stringify({ kind, body }));
    transition.mutate({ kind, key, body });
  }

  if (detail.isPending) return <p className="p-5 text-gray-500">불러오는 중…</p>;
  if (detail.error || !detail.data) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <section>
        <p role="alert" className="break-keep text-signal-red">
          {notFound ? "선적을 찾을 수 없습니다." : shipmentError(detail.error, "선적을 불러오지 못했습니다.")}
        </p>
        <Link to="/shipments" className="mt-3 inline-block text-sm underline">
          선적 목록으로
        </Link>
      </section>
    );
  }

  const shipment = detail.data;
  const base = baseVersion ?? shipment.version;
  const stale = shipment.version !== base;
  const isExport = isExportShipment(shipment);
  const kind = shipment.shipment_kind;
  const releaseName = releaseActionLabel(kind);
  const board = shipment.milestones;
  const canEditMilestones = can(shipment, "EDIT_MILESTONES");
  const hasMilestoneRows = board.rows.some((row) => row.kind === "STORED" && row.applicable && row.milestone_id !== null);

  /** 취소 409 — 통관·실적이 살아 있으면 무엇을 먼저 해야 하는지 이어 준다(막다른 길 0). */
  function cancelErrorNode(error: unknown): ReactNode {
    const text = shipmentError(error);
    const blockers = cancelBlockers(error);
    const go = (headingId: string) => {
      transition.reset();
      setAction(null);
      focusSection(headingId);
    };
    if (blockers.customs.length > 0) {
      return (
        <>
          {text}
          <span className="mt-1 block">먼저 삭제할 통관 기록: {blockers.customs.join(", ")}</span>
          <button type="button" onClick={() => go(CUSTOMS_HEADING_ID)} className="cell-nowrap mt-1 block underline">
            통관 기록으로 가기
          </button>
        </>
      );
    }
    if (blockers.actuals.length > 0) {
      return (
        <>
          {text}
          <span className="mt-1 block">먼저 정정(지우기)할 실적: {blockers.actuals.map(milestoneTypeLabel).join(", ")}</span>
          <button type="button" onClick={() => go(MILESTONES_HEADING_ID)} className="cell-nowrap mt-1 block underline">
            마일스톤으로 가기
          </button>
        </>
      );
    }
    return text;
  }

  return (
    <section>
      <Link to="/shipments" className="cell-nowrap text-sm text-gray-500 underline">
        ← 선적 목록
      </Link>
      <header className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex flex-wrap items-center gap-3 text-2xl font-bold">
            <span className="cell-nowrap">{shipment.doc_number}</span>
            <ShipmentStatusBadge status={shipment.status} kind={kind} />
            <span className="cell-nowrap rounded border border-gray-300 px-2 py-0.5 text-xs">
              {shipmentKindLabel(shipment.shipment_kind)}
            </span>
          </h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            {shipment.status === "PLANNED" &&
              `계획 중인 선적입니다. 라인 수량·출발/도착국을 고칠 수 있습니다. ${releaseName} 뒤에는 바꿀 수 없습니다.`}
            {shipment.status === "RELEASE_ORDERED" &&
              (isExport
                ? "출고지시된 선적입니다. 라인·국가는 동결되었습니다(바꾸려면 취소 후 새로 만듭니다). 피킹·검수·출고는 재고 기능(Phase 4)에서 이어집니다."
                : "선적 확정된 수입선적입니다(공급사 출하 지시). 라인·국가는 동결되었습니다(바꾸려면 취소 후 새로 만듭니다). 입고는 재고 기능(Phase 4)에서 이어집니다.")}
            {shipment.status === "CANCELLED" &&
              (isExport
                ? "취소된 선적입니다. 선적번호는 남고 다시 쓰이지 않으며, 가져간 수량은 원천 수주 잔량으로 돌아갔습니다."
                : "취소된 수입선적입니다. 선적번호는 남고 다시 쓰이지 않으며, 배정했던 수량은 원천 발주 라인의 배정 가능량으로 돌아갔습니다.")}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={reload} className="cell-nowrap rounded border border-gray-300 px-3 py-2 text-sm">
            최신 내용 불러오기
          </button>
          {can(shipment, "RELEASE_ORDER") && (
            <button
              type="button"
              onClick={() => openAction("release")}
              className="cell-nowrap rounded border border-gray-900 px-3 py-2 text-sm"
            >
              {releaseName}
            </button>
          )}
          {can(shipment, "CANCEL") && (
            <button
              type="button"
              onClick={() => openAction("cancel")}
              className="cell-nowrap rounded border border-signal-red px-3 py-2 text-sm text-signal-red"
            >
              취소
            </button>
          )}
          {can(shipment, "PLAN_DRAFT") && (
            <button
              type="button"
              onClick={requestDraft}
              disabled={draft.isPending}
              className={`cell-nowrap rounded px-3 py-2 text-sm disabled:opacity-50 ${
                hasMilestoneRows ? "border border-gray-300" : "bg-gray-900 text-white"
              }`}
            >
              {draft.isPending ? "처리 중…" : hasMilestoneRows ? "빠진 종류 채우기" : "계획 초안 만들기"}
            </button>
          )}
        </div>
      </header>

      {notice !== null && (
        <div role="alert" className="mt-4 rounded border border-signal-red p-3 text-sm text-signal-red">
          <p className="break-keep">{shipmentError(notice)}</p>
          {(isVersionConflict(notice) || needsBoardReload(notice)) && (
            <button type="button" onClick={reload} className="cell-nowrap mt-2 rounded border border-signal-red px-3 py-1">
              최신 내용 불러오기
            </button>
          )}
        </div>
      )}

      {stale && (
        <div role="status" className="mt-4 rounded border border-gray-400 p-3 text-sm">
          <p className="break-keep">
            다른 곳에서 이 선적이 수정되었습니다. 아래 내용은 최신이지만 편집 기준은 이전 내용입니다. 저장하면 충돌(409)로 거절됩니다.
          </p>
          <button type="button" onClick={reload} className="cell-nowrap mt-2 rounded border border-gray-400 px-3 py-1">
            최신 내용 불러오기
          </button>
        </div>
      )}

      <div className="mt-6 grid grid-cols-[minmax(0,1fr)] gap-6">
        <HeaderCard shipment={shipment} />

        {can(shipment, "EDIT_META") || can(shipment, "EDIT_COUNTRIES") ? (
          <MetaPanel
            key={`meta-${shipment.id}-${resetToken}`}
            shipment={shipment}
            version={base}
            onSaved={afterWrite}
            onError={setNotice}
          />
        ) : (
          <p className="break-keep text-sm text-gray-600">내부 메모: {show(shipment.internal_note)}</p>
        )}

        <section aria-labelledby={MILESTONES_HEADING_ID}>
          <h2 id={MILESTONES_HEADING_ID} tabIndex={-1} className="text-lg font-semibold">
            마일스톤
          </h2>
          <p className="mt-1 break-keep text-xs text-gray-500">
            계획·실적은 직접 입력하고, 적재기한·대금만기·제시기한은 서버가 다시 계산합니다(실적을 넣으면 바로 반영). D-N은 한국 날짜 기준입니다.
            {can(shipment, "PLAN_DRAFT") && " '계획 초안 만들기'는 빈 계획 행만 만듭니다(값은 직접 입력)."}
          </p>
          <div className="mt-2 flex flex-col gap-2">
            <HolidaySummaryBanner summary={board.holiday_summary} />
            <div role="status" aria-live="polite" className="break-keep text-sm text-gray-700">
              {announcements.map((text) => (
                <p key={text}>{text}</p>
              ))}
            </div>
            <MilestoneTimeline
              board={board}
              canEdit={canEditMilestones}
              onEdit={(mode, row) => setEditing({ mode, row })}
              customsSectionId={CUSTOMS_SECTION_ID}
              label="마일스톤"
            />
          </div>
        </section>

        <LinesSection
          key={`lines-${shipment.id}-${resetToken}`}
          shipment={shipment}
          version={base}
          onSaved={afterWrite}
          onError={setNotice}
        />

        <PartiesSection
          key={`parties-${shipment.id}-${resetToken}`}
          shipment={shipment}
          onSaved={(next) => afterWrite(next, false)}
          onError={setNotice}
        />

        <CustomsSection shipment={shipment} onChanged={refreshBoard} />

        <MilestoneChangesSection
          owner="SHIPMENT"
          ownerId={shipment.id}
          path={`/v1/shipments/${shipment.id}/milestone-changes`}
          withNotices
          canNotice={canEditMilestones}
          todayKst={board.today_kst}
          noun={NOUN}
          onNoticeSaved={refreshBoard}
          headingId="shipment-milestone-changes-title"
          title="마일스톤 변경 이력"
        />

        {isExport ? (
          <DocumentFlowPanel kind="SHIPMENT" id={shipment.id} />
        ) : (
          <p className="break-keep text-sm text-gray-500">
            수입선적은 문서 흐름(견적→PI→수주 사슬)에 없습니다. 원천 발주는 위 &lsquo;선적 정보&rsquo;의 링크로 봅니다.
          </p>
        )}

        <StatusTimeline
          basePath={`/v1/shipments/${shipment.id}`}
          queryKey={detailKey}
          statusLabel={(code) => shipmentStatusText(code, kind)}
        />
      </div>

      {action === "release" && can(shipment, "RELEASE_ORDER") && (
        <ConfirmDialog
          title={isExport ? "출고지시할까요?" : "선적을 확정할까요?"}
          confirmLabel={isExport ? "출고지시 확정" : "선적 확정"}
          description={
            <>
              {!isExport && <p className="mb-2">공급사에 출하를 지시한 수입선적으로 확정합니다.</p>}
              <p>
                {releaseName} 후에는 <strong>라인·국가를 바꿀 수 없습니다.</strong> 바꾸려면 취소 후 새로 만듭니다.
              </p>
              <p className="mt-2 text-gray-500">
                {isExport ? "피킹·검수·출고는 재고 기능(Phase 4)에서 이어집니다." : "입고는 재고 기능(Phase 4)에서 이어집니다."}
              </p>
            </>
          }
          pending={transition.isPending}
          error={transition.error ? shipmentError(transition.error) : null}
          onReload={isVersionConflict(transition.error) ? reload : undefined}
          onCancel={() => {
            transition.reset();
            setAction(null);
          }}
          onConfirm={() => submitAction("release", { version: base })}
        />
      )}
      {action === "cancel" && can(shipment, "CANCEL") && (
        <ConfirmDialog
          title="선적을 취소할까요?"
          danger
          confirmLabel="취소 확정"
          reasonLabel="취소 사유 (필수)"
          description={
            isExport ? (
              <p>
                취소하면 <strong>되돌릴 수 없습니다.</strong> 선적번호는 남고 다시 쓰이지 않으며, 이 선적이 가져간 수량은 원천 수주 잔량으로
                돌아갑니다. 수주의 마지막 살아 있는 선적이면 수주가 &lsquo;확정&rsquo;으로 돌아갑니다.
              </p>
            ) : (
              <p>
                취소하면 <strong>되돌릴 수 없습니다.</strong> 선적번호는 남고 다시 쓰이지 않으며, 이 수입선적이 배정한 수량은 원천 발주 라인의
                배정 가능량으로 돌아갑니다. 발주의 상태는 바뀌지 않습니다.
              </p>
            )
          }
          pending={transition.isPending}
          error={transition.error ? cancelErrorNode(transition.error) : null}
          onReload={isVersionConflict(transition.error) ? reload : undefined}
          onCancel={() => {
            transition.reset();
            setAction(null);
          }}
          onConfirm={(reason) => submitAction("cancel", { to_status: "CANCELLED", version: base, reason })}
        />
      )}
      {editing !== null && canEditMilestones && (
        <MilestoneValueDialog<MilestoneBoard>
          key={`${editing.mode}-${editing.row.milestone_type}`}
          mode={editing.mode}
          row={editing.row}
          basePath={`/v1/shipments/${shipment.id}/milestones`}
          noun={NOUN}
          defaultZone={shipment.origin_country_code === "KR" ? "Asia/Seoul" : null}
          todayKst={board.today_kst}
          noticeBasePath={`/v1/shipments/${shipment.id}/milestone-changes`}
          freezeActionLabel={releaseName}
          onSaved={(result) => applyBoard(result.board)}
          onNoticeSaved={refreshBoard}
          onClose={() => setEditing(null)}
          onReload={() => {
            setEditing(null);
            refreshBoard();
          }}
        />
      )}
    </section>
  );
}

// ── 선적 정보(읽기 — 원천 사본은 여기서 고치지 않는다) ─────────────────────────

function HeaderCard({ shipment }: { shipment: ShipmentDetail }) {
  const sourcePath =
    shipment.source.kind === "SALES_ORDER" ? `/sales-orders/${shipment.source.id}` : `/purchase-orders/${shipment.source.id}`;
  const sourceStatus =
    shipment.source.kind === "SALES_ORDER"
      ? salesOrderStatusLabel(shipment.source.status)
      : purchaseOrderStatusLabel(shipment.source.status);
  return (
    <section aria-label="선적 정보" className="rounded-lg border border-gray-200 p-4">
      <h2 className="text-lg font-semibold">선적 정보</h2>
      {!isExportShipment(shipment) && (
        <p className="mt-1 break-keep text-sm text-gray-500">
          수입선적은 발주의 단가·금액·통화·환율을 복사하지 않습니다(원가 비복사 — 금액은 발주 상세에서 원가 열람 권한으로만 봅니다).
        </p>
      )}
      <dl className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <DocField label="원천 전표">
          <Link to={sourcePath} className="cell-nowrap underline">
            {shipment.source.doc_number}
          </Link>{" "}
          <span className="cell-nowrap text-gray-500">({sourceStatus})</span>
        </DocField>
        <DocField label="거래 상대">{shipment.counterparty.name}</DocField>
        <DocField label="출발국 → 도착국">
          <span className="cell-nowrap">{countryText(shipment.origin_country_code)}</span>
          {" → "}
          <span className="cell-nowrap">{countryText(shipment.dest_country_code)}</span>
        </DocField>
        <DocField label="증빙일">
          <span className="num cell-nowrap">{shipment.doc_date}</span>
        </DocField>
        {/* 통화·고정 환율·합계 칸은 수출만 — 수입 응답엔 키 자체가 없다(R-5a-4: 빈칸·"(기준일 —)" 과도기 표시 제거). */}
        {isExportShipment(shipment) && (
          <>
            <DocField label="통화">{shipment.currency}</DocField>
            <DocField label="고정 환율">
              {shipment.fx_rate === null ? (
                EMPTY
              ) : (
                <span className="num cell-nowrap">
                  {shipment.fx_rate} (기준일 {show(shipment.fx_rate_date)})
                </span>
              )}
              <span className="mt-0.5 block text-xs text-gray-500">수주에서 고정 — 바꾸려면 취소 후 새로 만듭니다.</span>
            </DocField>
          </>
        )}
        <DocField label="결제조건">{paymentTermsText(shipment.payment_terms)}</DocField>
        <DocField label="인코텀즈">{incotermText(shipment.incoterm)}</DocField>
        {isExportShipment(shipment) && (
          <DocField label="합계">
            <span className="num cell-nowrap">
              {shipment.total_text} {shipment.currency}
            </span>
          </DocField>
        )}
        <DocField label="담당자">{shipment.assignee.display_name ?? `#${shipment.assignee.id}`}</DocField>
        <DocField label={`${releaseActionLabel(shipment.shipment_kind)} 시각`}>
          {shipment.frozen_at === null ? EMPTY : toKstDisplay(shipment.frozen_at)}
        </DocField>
        <DocField label="위험물(DG)">
          {shipment.dg_line_count > 0 ? (
            <>
              <span className="cell-nowrap rounded border border-signal-red px-1.5 py-0.5 text-xs text-signal-red">
                위험물 라인 {shipment.dg_line_count}개
              </span>
              <span className="mt-1 block text-xs text-gray-500">
                위험물 선적 점검은 담당자가 수동으로 확인합니다(자동 점검은 이후 단계).
              </span>
            </>
          ) : (
            "없음"
          )}
        </DocField>
        <DocField label="만든 시각">{toKstDisplay(shipment.created_at)}</DocField>
        <DocField label="수정 시각">{toKstDisplay(shipment.updated_at)}</DocField>
      </dl>
    </section>
  );
}

// ── 내부 메모·담당자·국가 — 바뀐 필드만 보낸다(무변경 저장 차단) ─────────────────

function MetaPanel({
  shipment,
  version,
  onSaved,
  onError,
}: {
  shipment: ShipmentDetail;
  version: number;
  onSaved: (next: ShipmentDetail) => void;
  onError: (error: unknown) => void;
}) {
  const { me } = useSession();
  const editMeta = can(shipment, "EDIT_META");
  const editCountries = can(shipment, "EDIT_COUNTRIES");
  // 담당자 선택 목록(`/users/lookup`)은 무역·관리자 전용 API다 — 물류는 메모·국가만 고치고 담당자는 읽기로 본다(서버 403 소음 방지).
  const canPickAssignee = editMeta && hasRole(me, "TRADE");
  const users = usePagedQuery<UserLookup>(["users", "lookup"], "/v1/users/lookup?size=200", canPickAssignee);
  const [note, setNote] = useState(shipment.internal_note ?? "");
  const [assignee, setAssignee] = useState(String(shipment.assignee.id));
  const [origin, setOrigin] = useState(shipment.origin_country_code);
  const [dest, setDest] = useState(shipment.dest_country_code);
  const lock = useRef(false);

  const body: Record<string, unknown> = {};
  if (editMeta && note.trim() !== (shipment.internal_note ?? "")) body.internal_note = note.trim() === "" ? null : note.trim();
  if (canPickAssignee && assignee !== String(shipment.assignee.id)) body.assignee_id = Number(assignee);
  if (editCountries && origin !== shipment.origin_country_code) body.origin_country_code = origin;
  if (editCountries && dest !== shipment.dest_country_code) body.dest_country_code = dest;
  const changed = Object.keys(body).length > 0;
  // 바꾼 국가만 검사한다 — 저장된 값이 이상해도 메모·담당 저장까지 막지 않는다(서버가 형식을 다시 본다).
  const originProblem = editCountries && origin !== shipment.origin_country_code ? countryProblem(origin) : null;
  const destProblem = editCountries && dest !== shipment.dest_country_code ? countryProblem(dest) : null;

  const save = useMutation({
    mutationFn: async () => {
      try {
        return await apiFetch<ShipmentDetail>(`/v1/shipments/${shipment.id}`, { method: "PATCH", body: { version, ...body } });
      } finally {
        lock.current = false;
      }
    },
    onSuccess: onSaved,
    onError,
  });

  const items = users.data?.items ?? [];
  const known = items.some((user) => String(user.id) === assignee);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (lock.current || !changed || originProblem !== null || destProblem !== null) return;
    lock.current = true;
    save.mutate();
  }

  return (
    <form aria-label="메모·담당·국가" className="rounded-lg border border-gray-200 p-4" onSubmit={submit}>
      <h2 className="text-lg font-semibold">메모·담당{editCountries ? "·국가" : ""}</h2>
      <p className="mt-1 break-keep text-sm text-gray-500">
        내부 메모·담당자는 상태와 무관하게 고칠 수 있습니다{editCountries ? ". 출발/도착국은 계획 중에만 고칠 수 있습니다" : ""}.
      </p>
      <fieldset disabled={save.isPending} className="contents">
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
          {editCountries && (
            <>
              <CountryInput label="출발국" value={origin} onChange={setOrigin} problem={originProblem} />
              <CountryInput label="도착국" value={dest} onChange={setDest} problem={destProblem} />
            </>
          )}
          {canPickAssignee ? (
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-gray-600">담당자</span>
              <select value={assignee} onChange={(e) => setAssignee(e.target.value)} className={inputClass}>
                {!known && <option value={assignee}>{shipment.assignee.display_name ?? `사용자 #${assignee}`}</option>}
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
          ) : (
            <div className="flex flex-col gap-1 text-sm">
              <span className="text-gray-600">담당자</span>
              <span className="break-keep">{shipment.assignee.display_name ?? `#${shipment.assignee.id}`}</span>
              {editMeta && <span className="break-keep text-xs text-gray-500">담당자 변경은 무역 담당·관리자가 합니다.</span>}
            </div>
          )}
          {editMeta ? (
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-gray-600">내부 메모</span>
              <textarea value={note} maxLength={1000} rows={2} onChange={(e) => setNote(e.target.value)} className={inputClass} />
            </label>
          ) : (
            <p className="break-keep text-sm text-gray-600">내부 메모: {show(shipment.internal_note)}</p>
          )}
        </div>
      </fieldset>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={save.isPending || !changed || originProblem !== null || destProblem !== null}
          className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm disabled:opacity-50"
        >
          {save.isPending ? "저장 중…" : "저장"}
        </button>
        {!changed && !save.isPending && <span className="text-sm text-gray-500">바뀐 내용이 없습니다.</span>}
      </div>
    </form>
  );
}

// ── 라인 ──────────────────────────────────────────────────────────────────

function LinesSection({
  shipment,
  version,
  onSaved,
  onError,
}: {
  shipment: ShipmentDetail;
  version: number;
  onSaved: (next: ShipmentDetail) => void;
  onError: (error: unknown) => void;
}) {
  const editable = can(shipment, "EDIT_LINES");
  const isExport = isExportShipment(shipment);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [removeTarget, setRemoveTarget] = useState<ShipmentLine | null>(null);
  const removeLock = useRef(false);

  const remove = useMutation({
    mutationFn: async (line: ShipmentLine) => {
      try {
        return await apiFetch<ShipmentDetail>(`/v1/shipments/${shipment.id}/lines/${line.id}?version=${version}`, {
          method: "DELETE",
        });
      } finally {
        removeLock.current = false;
      }
    },
    onSuccess: (next) => {
      setRemoveTarget(null);
      onSaved(next);
    },
    onError: (error) => {
      if (isVersionConflict(error)) {
        setRemoveTarget(null);
        onError(error);
      }
    },
  });

  // 수입은 단가·금액 열이 없다(응답에 키 자체가 없다 — 금액 칸 0, R-5a-4).
  const cols = (isExport ? 10 : 8) + (editable ? 1 : 0);

  return (
    <section aria-labelledby="shipment-lines-title">
      <h2 id="shipment-lines-title" className="text-lg font-semibold">
        라인
      </h2>
      <p className="mt-1 break-keep text-xs text-gray-500">
        {isExport
          ? "품목·단가는 수주에서 복사된 값입니다. ‘선적 잔량’은 이 선적까지 반영한 원천 수주 라인의 남은 수량입니다."
          : "품목은 발주에서 복사된 값입니다(단가·금액은 복사하지 않습니다). ‘배정 가능’은 이 선적까지 반영한 원천 발주 라인의 남은 배정 가능량입니다(발주 잔량과 다릅니다)."}
      </p>
      <div className="mt-2 overflow-x-auto rounded-lg border border-gray-200">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-gray-600">
            <tr>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">번호</th>
              <th scope="col" className="cell-nowrap px-3 py-2">SKU</th>
              <th scope="col" className="cell-nowrap min-w-32 px-3 py-2">품명</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">수량</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">원천 라인</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">{isExport ? "선적 잔량" : "배정 가능"}</th>
              {isExport && <th scope="col" className="cell-nowrap px-3 py-2 text-center">단가</th>}
              {isExport && <th scope="col" className="cell-nowrap px-3 py-2 text-center">금액</th>}
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">DG</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">가용재고</th>
              {editable && <th scope="col" className="cell-nowrap px-3 py-2 text-center">편집</th>}
            </tr>
          </thead>
          <tbody>
            {shipment.lines.map((line) =>
              editable && editingId === line.id ? (
                <LineEditRow
                  key={line.id}
                  shipment={shipment}
                  line={line}
                  version={version}
                  colSpan={cols}
                  onDone={(next) => {
                    setEditingId(null);
                    onSaved(next);
                  }}
                  onCancel={() => setEditingId(null)}
                  onError={onError}
                />
              ) : (
                <tr key={line.id} className="border-t border-gray-100 align-top">
                  <td className="num cell-nowrap px-3 py-2">{line.line_no}</td>
                  <td className="cell-nowrap px-3 py-2">{line.sku.code}</td>
                  <td className="break-keep px-3 py-2">{line.sku.name_ko}</td>
                  <td className="num cell-nowrap px-3 py-2">{line.quantity}</td>
                  <td className="num cell-nowrap px-3 py-2">
                    {`#${line.source_line.line_no} · ${isExport ? "수주" : "발주"} ${line.source_line.quantity}`}
                  </td>
                  <td className="num cell-nowrap px-3 py-2">{line.source_line.remaining_after}</td>
                  {/* 금액 칸은 수출 라인만 — 수입 라인엔 단가·금액·무상 키가 없다(키 존재로 다시 확인 — fail-closed). */}
                  {isExport && "line_amount_text" in line && (
                    <>
                      <td className="num cell-nowrap px-3 py-2">{line.is_free ? "무상" : (line.unit_price_text ?? "—")}</td>
                      <td className="num cell-nowrap px-3 py-2">{line.line_amount_text}</td>
                    </>
                  )}
                  <td className="px-3 py-2 text-center">
                    <DgBadge dg={line.dg} />
                  </td>
                  <td className="px-3 py-2 text-center">
                    {/* §8.3 '자리' — 숫자·0·현재고를 그리지 않는다(가용 산식은 S4-2). 서버가 다른 값을 주면 원문 대신 같은 배지. */}
                    <AvailabilityBadge />
                  </td>
                  {editable && (
                    <td className="cell-nowrap px-3 py-2 text-center">
                      <button type="button" onClick={() => setEditingId(line.id)} className="underline">
                        수정
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          remove.reset();
                          setRemoveTarget(line);
                        }}
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
          {isExportShipment(shipment) && (
            <tfoot>
              <tr className="border-t border-gray-200 bg-gray-50 font-semibold">
                <td colSpan={7} className="cell-nowrap px-3 py-2 text-right">
                  합계 (서버 계산)
                </td>
                <td className="num cell-nowrap px-3 py-2">
                  {shipment.total_text} {shipment.currency}
                </td>
                <td colSpan={editable ? 3 : 2} />
              </tr>
            </tfoot>
          )}
        </table>
      </div>

      {editable && <LineAddForm shipment={shipment} version={version} onDone={onSaved} onError={onError} />}

      {editable && removeTarget && (
        <ConfirmDialog
          title="라인을 제외할까요?"
          danger
          confirmLabel="제외"
          description={
            <p>
              {removeTarget.sku.code} {removeTarget.sku.name_ko} 라인을 제외합니다. 수량 {removeTarget.quantity}은{" "}
              {isExport ? "원천 수주 잔량" : "원천 발주 라인의 배정 가능량"}으로 돌아가고 라인 번호는 다시 쓰이지 않습니다. 마지막 라인은 제외할
              수 없습니다(선적 취소로).
            </p>
          }
          pending={remove.isPending}
          error={remove.error && !isVersionConflict(remove.error) ? shipmentError(remove.error) : null}
          onCancel={() => {
            remove.reset();
            setRemoveTarget(null);
          }}
          onConfirm={() => {
            if (removeLock.current) return;
            removeLock.current = true;
            remove.mutate(removeTarget);
          }}
        />
      )}
    </section>
  );
}

function LineEditRow({
  shipment,
  line,
  version,
  colSpan,
  onDone,
  onCancel,
  onError,
}: {
  shipment: ShipmentDetail;
  line: ShipmentLine;
  version: number;
  colSpan: number;
  onDone: (next: ShipmentDetail) => void;
  onCancel: () => void;
  onError: (error: unknown) => void;
}) {
  const client = useQueryClient();
  const isExport = isExportShipment(shipment);
  const [quantity, setQuantity] = useState(String(line.quantity));
  const [localError, setLocalError] = useState<unknown>(null);
  const lock = useRef(false);
  const quantityOk = isPositiveInt(quantity);
  const changed = quantity.trim() !== String(line.quantity);
  const sourceLineId = sourceLineIdOf(line);
  // 수출 = 잔량 초과(EXCEEDS_OPEN), 수입 = 배정 가능량 초과(EXCEEDS_ASSIGNABLE) — detail 값 = 이 라인 상한(남은 양 + 현재 수량).
  const open = sourceLineId === null ? undefined : quantityConflictByLine(localError, shipment.shipment_kind).get(sourceLineId);
  const hintId = `line-${line.id}-qty-hint`;

  const update = useMutation({
    mutationFn: async () => {
      try {
        return await apiFetch<ShipmentDetail>(`/v1/shipments/${shipment.id}/lines/${line.id}`, {
          method: "PATCH",
          body: { version, quantity: Number(quantity) },
        });
      } finally {
        lock.current = false;
      }
    },
    onSuccess: onDone,
    // 오류는 한 곳에만 — 낙관 잠금 충돌은 페이지 배너(최신 불러오기), 그 밖은 칸 아래.
    onError: (error) => {
      if (isVersionConflict(error)) onError(error);
      else setLocalError(error);
      // 잔량·배정 가능량 초과면 다른 선적이 먼저 가져갔다 — 원천·이 선적을 다시 받아 상한 안내(남은 양 + 현재)를 서버 값으로(low ⑥).
      if (quantityConflictByLine(error, shipment.shipment_kind).size > 0) refreshAfterConflict(client, shipment);
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
            update.mutate();
          }}
        >
          <span className="cell-nowrap font-medium">
            #{line.line_no} {line.sku.code}
          </span>
          <label className="flex flex-col gap-1">
            <span className="text-gray-600">수량</span>
            <input
              inputMode="numeric"
              value={quantity}
              disabled={update.isPending}
              aria-describedby={hintId}
              onChange={(e) => {
                setLocalError(null);
                setQuantity(e.target.value);
              }}
              className={`${inputClass} w-24 text-center`}
            />
          </label>
          <span id={hintId} className="cell-nowrap text-xs text-gray-500">
            {`상한 = ${isExport ? "원천 잔량" : "배정 가능"} ${line.source_line.remaining_after} + 현재 ${line.quantity}`}
          </span>
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
        {localError !== null && (
          <p role="alert" className="mt-2 break-keep text-sm text-signal-red">
            {shipmentError(localError)}
            {open !== undefined && ` — 이 라인 최대 수량 ${open}`}
          </p>
        )}
      </td>
    </tr>
  );
}

/** 라인 추가 후보(원천 전표 라인 중 이 선적에 없는 것) — 수출 = 수주 라인·선적 잔량, 수입 = 발주 라인·배정 가능량. */
interface AddCandidate {
  id: number;
  line_no: number;
  sku_code: string;
  sku_name_ko: string;
  /** 남은 양(서버 파생값) — 응답에 없으면 null('?'로 보이고 [전부] 버튼을 두지 않는다). */
  open: number | null;
}

/**
 * 라인 추가 — 원천 전표 라인 중 이 선적에 없는 것만, 남은 양 안에서(초과는 서버 409를 칸 아래에).
 * 수출 = 원천 수주의 선적 잔량, 수입(PR-5b) = 원천 발주(`GET /purchase-orders/{id}`) 라인의 배정 가능량 — 같은 경로 `POST /shipments/{id}/lines
 * {version, source_line_id, quantity}`(수입은 source_line_id = po_line_id).
 */
function LineAddForm({
  shipment,
  version,
  onDone,
  onError,
}: {
  shipment: ShipmentDetail;
  version: number;
  onDone: (next: ShipmentDetail) => void;
  onError: (error: unknown) => void;
}) {
  const client = useQueryClient();
  const isImport = shipment.source.kind === "PURCHASE_ORDER";
  const sourceId = shipment.source.id;
  const sourceNoun = isImport ? "발주" : "수주";
  const openNoun = isImport ? "배정 가능" : "선적 잔량";
  const source = useQuery<SalesOrderDetail | PurchaseOrderDetail, Error, AddCandidate[]>({
    queryKey: isImport ? purchaseOrderDetailKey(sourceId) : salesOrderDetailKey(sourceId),
    queryFn: () =>
      isImport
        ? apiFetch<PurchaseOrderDetail>(`/v1/purchase-orders/${sourceId}`)
        : apiFetch<SalesOrderDetail>(`/v1/sales-orders/${sourceId}`),
    // 원천 응답의 원가·금액 키는 후보로 옮기지 않는다(번호·SKU·남은 양만).
    select: (data) =>
      "po_kind" in data
        ? data.lines.map((line) => ({
            id: line.id,
            line_no: line.line_no,
            sku_code: line.sku_code,
            sku_name_ko: line.sku_name_ko,
            open: line.assignable_quantity ?? null,
          }))
        : data.lines.map((line) => ({
            id: line.id,
            line_no: line.line_no,
            sku_code: line.sku_code,
            sku_name_ko: line.sku_name_ko,
            open: line.shipment_open_quantity ?? null,
          })),
    staleTime: 0,
  });
  const [lineId, setLineId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [localError, setLocalError] = useState<unknown>(null);
  const lock = useRef(false);
  const [keys] = useState(() => createKeyKeeper());

  const present = new Set(shipment.lines.map(sourceLineIdOf));
  const candidates = (source.data ?? []).filter((line) => !present.has(line.id));
  const chosen = candidates.find((line) => String(line.id) === lineId);
  const remaining = chosen?.open ?? null;
  const open = chosen === undefined ? undefined : quantityConflictByLine(localError, shipment.shipment_kind).get(chosen.id);

  const add = useMutation({
    mutationFn: async (input: { key: string; body: Record<string, unknown> }) => {
      try {
        return await apiFetch<ShipmentDetail>(`/v1/shipments/${shipment.id}/lines`, {
          method: "POST",
          idempotencyKey: input.key,
          body: input.body,
        });
      } finally {
        lock.current = false;
      }
    },
    onSuccess: (next) => {
      keys.reset();
      setLineId("");
      setQuantity("");
      setLocalError(null);
      onDone(next);
    },
    onError: (error) => {
      if (isVersionConflict(error)) onError(error);
      else setLocalError(error);
      // 잔량·배정 가능량 초과면 원천을 다시 받아 드롭다운의 남은 양·[전부] 버튼을 서버 값으로(low ⑥).
      if (quantityConflictByLine(error, shipment.shipment_kind).size > 0) refreshAfterConflict(client, shipment);
    },
  });

  const ready = chosen !== undefined && isPositiveInt(quantity);

  return (
    <form
      aria-label="라인 추가"
      className="mt-3 rounded-lg border border-gray-200 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!ready || lock.current || chosen === undefined) return;
        lock.current = true;
        const body = { version, source_line_id: chosen.id, quantity: Number(quantity) };
        add.mutate({ key: keys.keyFor(JSON.stringify(body)), body });
      }}
    >
      <h3 className="font-medium">라인 추가</h3>
      <p className="mt-1 break-keep text-xs text-gray-500">
        {isImport
          ? `원천 발주 ${shipment.source.doc_number}의 라인 중 이 선적에 없는 라인만, 배정 가능량 안에서 추가합니다(품목은 발주에서 복사 — 단가·금액은 복사하지 않습니다).`
          : `원천 수주 ${shipment.source.doc_number}의 라인 중 이 선적에 없는 라인만, 선적 잔량 안에서 추가합니다(품목·단가는 수주에서 복사).`}
      </p>
      {source.error ? (
        <p role="alert" className="mt-2 break-keep text-sm text-signal-red">
          {shipmentError(source.error, `원천 ${sourceNoun} 라인을 불러오지 못했습니다.`)}
        </p>
      ) : source.isPending ? (
        <p className="mt-2 text-sm text-gray-500">원천 {sourceNoun} 라인을 불러오는 중…</p>
      ) : candidates.length === 0 ? (
        <p className="mt-2 break-keep text-sm text-gray-500">추가할 수 있는 원천 라인이 없습니다(모든 라인이 이미 이 선적에 있습니다).</p>
      ) : (
        <fieldset disabled={add.isPending} className="contents">
          <div className="mt-3 flex flex-wrap items-end gap-3 text-sm">
            <label className="flex flex-col gap-1">
              <span className="text-gray-600">원천 라인</span>
              <select
                value={lineId}
                onChange={(e) => {
                  setLocalError(null);
                  setLineId(e.target.value);
                }}
                className={inputClass}
              >
                <option value="">선택</option>
                {candidates.map((line) => (
                  <option key={line.id} value={line.id}>
                    #{line.line_no} {line.sku_code} {line.sku_name_ko} — {openNoun} {line.open ?? "?"}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-gray-600">수량</span>
              <input
                inputMode="numeric"
                value={quantity}
                onChange={(e) => {
                  setLocalError(null);
                  setQuantity(e.target.value);
                }}
                className={`${inputClass} w-24 text-center`}
              />
            </label>
            {remaining !== null && (
              <button
                type="button"
                disabled={remaining <= 0}
                onClick={() => {
                  setLocalError(null);
                  setQuantity(String(remaining));
                }}
                className="cell-nowrap rounded border border-gray-300 px-3 py-2 disabled:opacity-50"
              >
                {isImport ? "배정 가능 전부" : "잔량 전부"}
              </button>
            )}
            <button
              type="submit"
              disabled={!ready || add.isPending}
              className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-white disabled:opacity-50"
            >
              {add.isPending ? "추가 중…" : "라인 추가"}
            </button>
          </div>
        </fieldset>
      )}
      {quantity.trim() !== "" && !isPositiveInt(quantity) && (
        <p role="alert" className="mt-2 text-sm text-signal-red">
          수량은 1 이상의 정수로 입력해 주세요.
        </p>
      )}
      {localError !== null && (
        <p role="alert" className="mt-2 break-keep text-sm text-signal-red">
          {shipmentError(localError)}
          {open !== undefined && (isImport ? ` — 남은 배정 가능량 ${open}` : ` — 원천 남은 수량 ${open}`)}
        </p>
      )}
    </form>
  );
}

// ── 당사자 ───────────────────────────────────────────────────────────────

function PartiesSection({
  shipment,
  onSaved,
  onError,
}: {
  shipment: ShipmentDetail;
  onSaved: (next: ShipmentDetail) => void;
  onError: (error: unknown) => void;
}) {
  const editable = can(shipment, "EDIT_PARTIES");
  const [removeTarget, setRemoveTarget] = useState<ShipmentParty | null>(null);
  const removeLock = useRef(false);

  const remove = useMutation({
    mutationFn: async (party: ShipmentParty) => {
      try {
        return await apiFetch<ShipmentDetail>(`/v1/shipments/${shipment.id}/parties/${party.id}?version=${party.version}`, {
          method: "DELETE",
        });
      } finally {
        removeLock.current = false;
      }
    },
    onSuccess: (next) => {
      setRemoveTarget(null);
      onSaved(next);
    },
    onError: (error) => {
      if (isVersionConflict(error)) {
        setRemoveTarget(null);
        onError(error);
      }
    },
  });

  return (
    <section aria-labelledby="shipment-parties-title">
      <h2 id="shipment-parties-title" className="text-lg font-semibold">
        당사자
      </h2>
      <p className="mt-1 break-keep text-xs text-gray-500">
        서류에 쓰는 영문 이름·주소는 지정 시점의 거래처 값이 복사됩니다(거래처를 나중에 고쳐도 바뀌지 않습니다).{" "}
        {shipment.source.kind === "PURCHASE_ORDER"
          ? "송하인은 발주 공급사에서 자동으로 복사되고, 수하인은 자사입니다."
          : "수하인은 수주 바이어에서 자동으로 복사됩니다."}
      </p>
      <div className="mt-2 overflow-x-auto rounded-lg border border-gray-200">
        {shipment.parties.length === 0 ? (
          <p className="p-4 text-sm text-gray-500">지정된 당사자가 없습니다.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th scope="col" className="cell-nowrap px-3 py-2 text-center">역할</th>
                <th scope="col" className="cell-nowrap min-w-40 px-3 py-2">거래처(영문)</th>
                <th scope="col" className="cell-nowrap min-w-48 px-3 py-2">주소(영문)</th>
                <th scope="col" className="cell-nowrap px-3 py-2 text-center">출처</th>
                {editable && <th scope="col" className="cell-nowrap px-3 py-2 text-center">편집</th>}
              </tr>
            </thead>
            <tbody>
              {shipment.parties.map((party) => (
                <tr key={party.id} className="border-t border-gray-100 align-top">
                  <td className="cell-nowrap px-3 py-2 text-center">{partyRoleLabel(party.role)}</td>
                  <td className="break-keep px-3 py-2">{party.name_en}</td>
                  <td className="whitespace-pre-line break-keep px-3 py-2 text-gray-700">{party.address_en ?? EMPTY}</td>
                  <td className="cell-nowrap px-3 py-2 text-center text-xs text-gray-500">{party.auto ? "원천에서 복사" : "지정"}</td>
                  {editable && (
                    <td className="cell-nowrap px-3 py-2 text-center">
                      {party.auto ? (
                        <span className="text-xs text-gray-400">—</span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            remove.reset();
                            setRemoveTarget(party);
                          }}
                          className="text-gray-500 underline"
                        >
                          제외
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {editable && <PartyAddForm shipment={shipment} onDone={onSaved} />}

      {editable && removeTarget && (
        <ConfirmDialog
          title="당사자를 제외할까요?"
          confirmLabel="제외"
          description={
            <p>
              {partyRoleLabel(removeTarget.role)} {removeTarget.name_en}을(를) 이 선적에서 제외합니다. 같은 역할로 다른 거래처를 다시 지정할 수
              있습니다.
            </p>
          }
          pending={remove.isPending}
          error={remove.error && !isVersionConflict(remove.error) ? shipmentError(remove.error) : null}
          onCancel={() => {
            remove.reset();
            setRemoveTarget(null);
          }}
          onConfirm={() => {
            if (removeLock.current) return;
            removeLock.current = true;
            remove.mutate(removeTarget);
          }}
        />
      )}
    </section>
  );
}

function PartyAddForm({ shipment, onDone }: { shipment: ShipmentDetail; onDone: (next: ShipmentDetail) => void }) {
  const taken = new Set(shipment.parties.map((party) => party.role));
  const roles = SELECTABLE_PARTY_ROLES.filter((role) => !taken.has(role));
  const [role, setRole] = useState<SelectablePartyRole | "">("");
  const [partner, setPartner] = useState<Partner | null>(null);
  const lock = useRef(false);
  const [keys] = useState(() => createKeyKeeper());

  const add = useMutation({
    mutationFn: async (input: { key: string; body: { role: SelectablePartyRole; partner_id: number } }) => {
      try {
        return await apiFetch<ShipmentDetail>(`/v1/shipments/${shipment.id}/parties`, {
          method: "POST",
          idempotencyKey: input.key,
          body: input.body,
        });
      } finally {
        lock.current = false;
      }
    },
    onSuccess: (next) => {
      keys.reset();
      setRole("");
      setPartner(null);
      onDone(next);
    },
  });

  if (roles.length === 0) {
    return <p className="mt-3 break-keep text-sm text-gray-500">통지처·포워더·관세사가 모두 지정되어 있습니다(바꾸려면 먼저 제외).</p>;
  }

  const partnerType = role === "" ? null : PARTY_PARTNER_TYPE[role];
  const ready = role !== "" && partner !== null;

  return (
    <form
      aria-label="당사자 추가"
      className="mt-3 rounded-lg border border-gray-200 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!ready || lock.current) return;
        lock.current = true;
        const body = { role, partner_id: partner.id };
        add.mutate({ key: keys.keyFor(JSON.stringify(body)), body });
      }}
    >
      <h3 className="font-medium">당사자 추가</h3>
      <fieldset disabled={add.isPending} className="contents">
        <div className="mt-3 grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
          <label className="flex flex-col gap-1">
            <span className="text-gray-600">역할</span>
            <select
              value={role}
              onChange={(e) => {
                add.reset();
                setPartner(null); // 역할이 바뀌면 거래처 유형 조건이 달라진다 — 앞 선택을 버린다.
                setRole(e.target.value as SelectablePartyRole | "");
              }}
              className={inputClass}
            >
              <option value="">선택</option>
              {roles.map((code) => (
                <option key={code} value={code}>
                  {partyRoleLabel(code)}
                </option>
              ))}
            </select>
          </label>
          <div className="flex flex-col gap-1 sm:col-span-2">
            <span className="text-gray-600">거래처{partnerType === null ? "" : ` (${partyRoleLabel(role)} 유형만)`}</span>
            {role === "" ? (
              <span className="text-xs text-gray-500">역할을 먼저 고르세요.</span>
            ) : (
              <SearchSelect<Partner>
                key={role}
                label={`${partyRoleLabel(role)} 거래처`}
                path="/v1/partners"
                params={partnerType === null ? undefined : { type: partnerType }}
                queryKey={["partners"]}
                value={partner}
                onChange={(next) => {
                  add.reset();
                  setPartner(next);
                }}
                getKey={(item) => item.id}
                getLabel={(item) => `${item.name_ko}${item.name_en ? ` (${item.name_en})` : " — 영문명 없음"}`}
              />
            )}
          </div>
        </div>
      </fieldset>
      {add.error && (
        <p role="alert" className="mt-2 break-keep text-sm text-signal-red">
          {shipmentError(add.error)}
          <PartnerFixHint error={add.error} />
        </p>
      )}
      <button
        type="submit"
        disabled={!ready || add.isPending}
        className="cell-nowrap mt-3 rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
      >
        {add.isPending ? "추가 중…" : "당사자 추가"}
      </button>
    </form>
  );
}
