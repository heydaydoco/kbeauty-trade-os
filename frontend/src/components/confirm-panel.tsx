// 수주 확정 패널 (S3-1 PR-12b — design-D D5 / ADR-0070·0069). SO 상세 GatePanel 아래에서 확정·승인 요청·여신 평가·확정 증거를 보인다.
//
// 규칙(서버가 정본, 화면은 편의 — §18.1):
// - ★ 프런트는 통과·미통과·승인 필요 여부를 다시 판정하지 않는다. 버튼은 접수(RECEIVED) SO에서 항상 서버를 부르고, 서버의 409 `blocked_gates[]`·`resolution`·`can_override`·
//   `pending_approval_*`·GET gates의 `clearance`·`settlement`를 그대로 표시한다.
// - 멱등 키: 확정·승인 요청 모두 (SO id, version)당 1개 — 본문 `{version}` 기준 Map으로 재사용(같은 version 재시도=같은 키, version이 오르면 새 키), 성공하면 비운다.
//   거부(409 GATE_BLOCKED)는 서버가 키를 소비하지 않으므로 같은 version에서 승인만 받은 뒤의 재시도는 같은 키를 쓴다.
// - 더블클릭 잠금(ref)은 mutationFn의 finally에서 푼다. 요청 중 Esc/닫기는 무시한다. `networkMode:"always"`.
// - 다이얼로그·차단 결과·승인 상태는 컴포넌트 상태다 — 창 포커스 재조회로 목록이 바뀌어도 닫히거나 입력이 지워지지 않는다.
// - 쓰기(확정) 뒤 SO 기준 version(baseVersion)은 자동으로 옮기지 않는다 — '최신 내용 불러오기'(상위 onReload)로만.
// - 확정 후에는 읽기 전용: 서버가 준 증적 3열·증거 id만 보인다.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../lib/api";
import { APPROVALS_QUERY_KEY, SELF_DECISION_NOTICE, approvalStatusLabel } from "../lib/approval";
import {
  allocationLabel,
  approvalRequestErrorMessage,
  confirmErrorMessage,
  creditVerdictLabel,
  isApprovalRequestRecoverable,
  isConfirmRecoverable,
  keyFor,
  parseBlockedDetail,
  piVerdictLabel,
  resolutionGuide,
  type ApprovalRequestResult,
  type BlockedDetail,
  type ConfirmResult,
} from "../lib/confirm";
import { toKstDisplay } from "../lib/datetime";
import { salesOrderStatusLabel } from "../lib/doc-status";
import { gateTargetLabel, gatesKey, isRouteForbidden, levelBadgeClass, levelLabel, newGateKey, useGateReport } from "../lib/gate";
import { PROFORMAS_QUERY_KEY } from "../lib/proforma";
import { QUOTATIONS_QUERY_KEY } from "../lib/quotation";
import { DOCUMENT_FLOW_QUERY_KEY, SALES_ORDERS_QUERY_KEY, type SalesOrderDetail } from "../lib/sales-order";
import { hasRole, useSession } from "../lib/session";
import { ConfirmDialog } from "./confirm-dialog";
import { CreditEvaluationCard } from "./credit-evaluation-card";

type DialogKind = "confirm" | "request";
interface Pending {
  id: number;
  status: string;
}

const HANGUL = /[가-힣]/;
const OPEN_STATUS = "RECEIVED";

export function ConfirmPanel({ so, version, onReload }: { so: SalesOrderDetail; version: number; onReload: () => void }) {
  const { me } = useSession();
  const client = useQueryClient();
  const [forbidden, setForbidden] = useState(false);
  const canWrite = hasRole(me, "TRADE") && !forbidden;
  const open = so.status === OPEN_STATUS;

  const report = useGateReport(so.id);
  const gates = Array.isArray(report.data?.gates) ? report.data.gates : null;
  const creditReport = gates?.find((g) => g.gate_code === "CREDIT") ?? null;

  const [dialog, setDialog] = useState<DialogKind | null>(null);
  const [blocked, setBlocked] = useState<BlockedDetail | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [result, setResult] = useState<ConfirmResult | null>(null);
  const [requested, setRequested] = useState<ApprovalRequestResult | null>(null);
  const [announce, setAnnounce] = useState<{ text: string; n: number } | null>(null);

  // 결과 영역으로 포커스를 옮긴다 — 다이얼로그가 닫히고 버튼이 사라져도 포커스가 body로 빠지지 않게.
  const resultRef = useRef<HTMLDivElement | null>(null);
  const blockedRef = useRef<HTMLDivElement | null>(null);
  const requestedRef = useRef<HTMLDivElement | null>(null);
  const [focusTarget, setFocusTarget] = useState<{ which: "result" | "blocked" | "requested"; n: number } | null>(null);
  useEffect(() => {
    if (focusTarget === null) return;
    const ref = focusTarget.which === "result" ? resultRef : focusTarget.which === "blocked" ? blockedRef : requestedRef;
    ref.current?.focus();
  }, [focusTarget]);
  const focusOn = (which: "result" | "blocked" | "requested") => setFocusTarget((prev) => ({ which, n: (prev?.n ?? 0) + 1 }));
  const say = (text: string) => setAnnounce((prev) => ({ text, n: (prev?.n ?? 0) + 1 }));

  const confirmKeys = useRef(new Map<string, string>());
  const requestKeys = useRef(new Map<string, string>());
  const confirmLock = useRef(false);
  const requestLock = useRef(false);

  const confirm = useMutation({
    // 오프라인이어도 요청을 보내 본다 — 기본(online)은 요청이 paused로 멈춰 다이얼로그가 갇힌다.
    networkMode: "always",
    // ★ 잠금 해제는 finally — 요청 중 reset()·언마운트에도 풀린다.
    mutationFn: async (input: { version: number; key: string }) => {
      try {
        return await apiFetch<ConfirmResult>(`/v1/sales-orders/${so.id}/confirm`, {
          method: "POST",
          idempotencyKey: input.key,
          body: { version: input.version },
        });
      } finally {
        confirmLock.current = false;
      }
    },
    onSuccess: (data) => {
      setDialog(null);
      setBlocked(null);
      setResult(data);
      confirmKeys.current.clear();
      say("수주를 확정했습니다.");
      focusOn("result");
      // 확정은 SO(상세·목록·게이트)·문서 흐름·QT(수주전환)·PI·승인(소비)을 모두 바꾼다.
      void client.invalidateQueries({ queryKey: SALES_ORDERS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: DOCUMENT_FLOW_QUERY_KEY });
      void client.invalidateQueries({ queryKey: QUOTATIONS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: PROFORMAS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: APPROVALS_QUERY_KEY });
    },
    onError: (error) => {
      const detail = parseBlockedDetail(error);
      if (detail !== null) {
        // 미해소 — 서버가 BLOCKED 증거를 남기고 409. 다이얼로그를 닫고 게이트별 안내를 보인다(키는 유지: 같은 version 재시도는 같은 키).
        setDialog(null);
        setBlocked(detail);
        setPending(detail.pending_approval_id !== null ? { id: detail.pending_approval_id, status: detail.pending_approval_status ?? "" } : null);
        say(`수주가 확정되지 않았습니다. 해소되지 않은 게이트가 ${detail.blocked_gates.length}건 있습니다.`);
        focusOn("blocked");
        void client.invalidateQueries({ queryKey: gatesKey(so.id) });
        return;
      }
      if (isRouteForbidden(error)) {
        setForbidden(true);
        setDialog(null);
        return;
      }
      // 실패(충돌·낡음·네트워크 등) 뒤에는 판정을 다시 읽는다 — 화면이 옛 판정을 들고 있지 않게.
      void client.invalidateQueries({ queryKey: gatesKey(so.id) });
    },
  });

  const request = useMutation({
    networkMode: "always",
    mutationFn: async (input: { version: number; key: string }) => {
      try {
        return await apiFetch<ApprovalRequestResult>(`/v1/sales-orders/${so.id}/approval-requests`, {
          method: "POST",
          idempotencyKey: input.key,
          body: { version: input.version },
        });
      } finally {
        requestLock.current = false;
      }
    },
    onSuccess: (view) => {
      setDialog(null);
      setRequested(view);
      setPending({ id: view.id, status: view.status });
      requestKeys.current.clear();
      say(
        view.created
          ? `승인 #${view.id}을 요청했습니다. 결재가 끝나면 다시 확정하세요.`
          : `같은 내용의 진행 중인 승인 #${view.id}이 이미 있어 새로 만들지 않았습니다.`,
      );
      focusOn("requested");
      void client.invalidateQueries({ queryKey: gatesKey(so.id) });
      void client.invalidateQueries({ queryKey: APPROVALS_QUERY_KEY });
    },
    onError: (error) => {
      if (isRouteForbidden(error)) {
        setForbidden(true);
        setDialog(null);
        return;
      }
      void client.invalidateQueries({ queryKey: gatesKey(so.id) });
    },
  });

  function submitConfirm() {
    if (confirmLock.current) return;
    // 키를 먼저 만든다 — 키 생성이 던져도 잠금이 서지 않아 고착되지 않는다.
    const key = keyFor(confirmKeys.current, JSON.stringify({ version }), newGateKey);
    confirmLock.current = true;
    confirm.mutate({ version, key });
  }

  function submitRequest() {
    if (requestLock.current) return;
    const key = keyFor(requestKeys.current, JSON.stringify({ version }), newGateKey);
    requestLock.current = true;
    request.mutate({ version, key });
  }

  /** 충돌 안내의 '최신 내용 불러오기' — 다이얼로그를 닫고 상위(SO 상세)가 기준 version·판정을 다시 읽게 한다. */
  function reload() {
    confirm.reset();
    request.reset();
    setDialog(null);
    setBlocked(null);
    void client.invalidateQueries({ queryKey: gatesKey(so.id) });
    onReload();
  }

  function openDialog(kind: DialogKind) {
    confirm.reset();
    request.reset();
    setDialog(kind);
  }

  // 접수가 아니게 되어도(남이 처리) 요청 중·오류 표시 중에는 다이얼로그를 닫지 않는다 — 입력·오류 안내가 사라지지 않게(서버가 최종).
  const confirmDialogOpen = dialog === "confirm" && (open || confirm.isPending || confirm.isError);
  const requestDialogOpen = dialog === "request" && (open || request.isPending || request.isError);

  const creditBlocked = blocked?.blocked_gates.find((g) => g.gate_code === "CREDIT") ?? null;
  const creditRow = creditBlocked ?? creditReport;
  // 승인 요청 버튼: 서버가 CREDIT의 해소 수단을 APPROVAL로 주고 미해소(GET의 settlement)일 때만. GET이 없을 때는 확정 거부 응답의 값.
  const creditNeedsApproval =
    open && (creditReport !== null ? creditReport.resolution === "APPROVAL" && creditReport.settlement === "UNRESOLVED" : creditBlocked?.resolution === "APPROVAL");
  const cardApprovalId = pending?.id ?? report.data?.approval?.approval_id ?? null;
  const confirmedAt = so.confirmed_at ?? result?.sales_order.confirmed_at ?? null;
  const evidenceSo = so.confirmed_at !== null ? so : (result?.sales_order ?? null);

  return (
    <section aria-labelledby="confirm-panel-title" className="rounded-lg border border-gray-200 p-4">
      <h2 id="confirm-panel-title" className="text-lg font-semibold">
        수주 확정
      </h2>

      <p role="status" aria-label="확정 처리 결과" className={announce ? "mt-2 break-keep text-sm font-medium" : "sr-only"}>
        {announce?.text ?? ""}
      </p>

      {result !== null && <ConfirmResultView result={result} onReload={onReload} regionRef={resultRef} />}

      {open && (
        <div className="mt-3 grid gap-3">
          <p className="break-keep text-sm text-gray-700">
            확정 전 게이트 상태를 확인하세요. 확정하면 서버가 게이트 7종을 다시 평가하고, 해소되지 않은 항목이 있으면 확정되지 않습니다.
          </p>
          <ReportHint loading={report.isPending} error={report.isError} cleared={report.data?.clearance?.cleared} unresolved={report.data?.clearance?.unresolved_count} />
          {canWrite ? (
            <div>
              <button
                type="button"
                onClick={() => openDialog("confirm")}
                className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white"
              >
                수주 확정
              </button>
            </div>
          ) : (
            <p role="note" className="break-keep text-sm text-gray-600">
              수주 확정은 무역·관리자만 할 수 있습니다. 게이트 상태는 위 표에서 확인할 수 있습니다.
            </p>
          )}
        </div>
      )}

      {!open && confirmedAt === null && (
        <p role="note" className="mt-3 break-keep text-sm text-gray-600">
          {so.status === "ON_HOLD"
            ? "보류 중인 수주는 확정할 수 없습니다. 재개한 뒤 확정하세요."
            : `${salesOrderStatusLabel(so.status)} 상태의 수주는 확정할 수 없습니다.`}
        </p>
      )}

      {blocked !== null && open && <BlockedView detail={blocked} regionRef={blockedRef} />}

      {creditNeedsApproval && canWrite && (
        <ApprovalRequestSection
          pending={pending}
          requested={requested}
          regionRef={requestedRef}
          onRequest={() => openDialog("request")}
        />
      )}

      {open && creditRow !== null && (
        <div className="mt-4">
          <CreditEvaluationCard row={creditRow} source={creditBlocked !== null ? "blocked" : "report"} approvalId={cardApprovalId} />
        </div>
      )}

      {evidenceSo !== null && confirmedAt !== null && <ConfirmEvidence so={evidenceSo} confirmedAt={confirmedAt} />}

      {confirmDialogOpen && (
        <ConfirmDialog
          title="수주를 확정할까요?"
          danger
          confirmLabel="수주 확정"
          description={
            <div className="grid gap-2">
              <p>
                <strong>확정하면 되돌릴 수 없습니다.</strong>
              </p>
              <ul className="list-disc pl-5">
                <li>확정 후에는 단가·환율·결제조건·라인이 동결되어 고칠 수 없습니다(바꾸려면 취소 후 새 수주).</li>
                <li>연결된 견적(QT)이 &apos;수주전환&apos;으로 표시됩니다.</li>
                <li>서버가 게이트 7종을 다시 평가합니다. 해소되지 않은 항목이 있으면 확정되지 않으며 관리자도 예외가 없습니다.</li>
                <li>같은 요청을 다시 보내도 중복 확정되지 않습니다.</li>
              </ul>
            </div>
          }
          pending={confirm.isPending}
          error={confirm.error ? confirmErrorMessage(confirm.error) : null}
          onReload={isConfirmRecoverable(confirm.error) ? reload : undefined}
          onCancel={() => {
            // 요청 중 닫기(Esc)는 무시 — 닫고 reset하면 응답을 못 본 채 같은 키로 재시도하게 된다.
            if (confirm.isPending) return;
            confirm.reset();
            setDialog(null);
          }}
          onConfirm={submitConfirm}
        />
      )}

      {requestDialogOpen && (
        <ConfirmDialog
          title={pending?.status === "APPROVED" ? "승인을 다시 요청할까요?" : "승인을 요청할까요?"}
          confirmLabel="승인 요청"
          description={
            <div className="grid gap-2">
              <p>여신 한도를 넘는 이 수주를 확정하려면 결재 자격자의 승인이 필요합니다. 요청 시점의 여신 평가가 승인 근거로 동결됩니다.</p>
              <ul className="list-disc pl-5">
                <li>같은 내용의 진행 중인 승인이 이미 있으면 새로 만들지 않고 그 승인을 보여 줍니다.</li>
                <li>승인된 뒤 수량·단가·환율·라인이 바뀌면 승인은 무효가 되어 다시 요청해야 합니다.</li>
                <li>{SELF_DECISION_NOTICE} 다른 결재 자격자가 처리합니다.</li>
              </ul>
            </div>
          }
          pending={request.isPending}
          error={request.error ? approvalRequestErrorMessage(request.error) : null}
          onReload={isApprovalRequestRecoverable(request.error) ? reload : undefined}
          onCancel={() => {
            if (request.isPending) return;
            request.reset();
            setDialog(null);
          }}
          onConfirm={submitRequest}
        />
      )}
    </section>
  );
}

function ReportHint({ loading, error, cleared, unresolved }: { loading: boolean; error: boolean; cleared: boolean | undefined; unresolved: number | undefined }) {
  if (loading) return <p role="status" className="text-sm text-gray-500">게이트 상태를 불러오는 중…</p>;
  if (error || cleared === undefined) {
    return <p className="break-keep text-sm text-gray-500">게이트 상태를 불러오지 못했습니다. 그래도 확정은 서버가 다시 평가해 판정합니다.</p>;
  }
  return (
    <p className="break-keep text-sm text-gray-700">
      서버 참고 판정: {cleared ? "확정 가능 후보입니다(모든 게이트 해소)." : `미해소 게이트 ${unresolved ?? 0}건이 있습니다.`} 최종 판정은 확정 시 서버가 합니다.
    </p>
  );
}

function BlockedView({ detail, regionRef }: { detail: BlockedDetail; regionRef: React.RefObject<HTMLDivElement | null> }) {
  return (
    <div
      ref={regionRef}
      tabIndex={-1}
      role="region"
      aria-label="확정 차단 결과"
      className="mt-4 rounded border border-signal-red p-3 text-sm focus:outline focus:outline-2 focus:outline-gray-900"
    >
      <p className="break-keep font-medium text-signal-red">
        수주가 확정되지 않았습니다. 해소되지 않은 게이트 {detail.blocked_gates.length}건 (확정을 시도한 시점의 서버 평가)
      </p>
      {detail.blocked_gates.length === 0 ? (
        <p className="mt-2 break-keep text-gray-700">해소되지 않은 항목의 자세한 내용을 받지 못했습니다. 위 게이트 판정 표를 확인하세요.</p>
      ) : (
        <ul className="mt-2 grid gap-3">
          {detail.blocked_gates.map((gate) => (
            <li key={`${gate.gate_code}:${gate.line_id ?? "-"}:${gate.basis_hash}`} className="rounded border border-gray-200 p-2">
              <p className="break-keep">
                <strong className="cell-nowrap">{gateTargetLabel(gate)}</strong>{" "}
                <span className={`cell-nowrap rounded border px-2 py-0.5 text-xs ${levelBadgeClass(gate.level)}`}>{levelLabel(gate.level)}</span>
              </p>
              {gate.message_ko !== "" && <p className="mt-1 break-keep">{gate.message_ko}</p>}
              <p className="mt-1 break-keep text-gray-600">{resolutionGuide(gate)}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ApprovalRequestSection({
  pending,
  requested,
  regionRef,
  onRequest,
}: {
  pending: Pending | null;
  requested: ApprovalRequestResult | null;
  regionRef: React.RefObject<HTMLDivElement | null>;
  onRequest: () => void;
}) {
  const waiting = pending !== null && pending.status === "REQUESTED";
  const stale = pending !== null && pending.status === "APPROVED";
  const otherActive = pending !== null && !waiting && !stale;
  return (
    <div ref={regionRef} tabIndex={-1} role="region" aria-label="승인 요청" className="mt-4 rounded border border-gray-200 p-3 text-sm focus:outline focus:outline-2 focus:outline-gray-900">
      <h3 className="font-semibold">승인 요청</h3>
      {requested !== null && (
        <p className="mt-1 break-keep font-medium">
          {requested.created ? "승인을 요청했습니다." : "같은 내용의 진행 중인 승인이 이미 있어 새로 만들지 않았습니다."}{" "}
          <Link to={`/approvals/${requested.id}`} className="cell-nowrap underline">
            승인 #{requested.id} 보기
          </Link>{" "}
          <span className="cell-nowrap rounded border border-gray-300 px-2 py-0.5 text-xs">{approvalStatusLabel(requested.status)}</span>
        </p>
      )}
      {waiting && requested === null && (
        <p className="mt-1 break-keep">
          승인 #{pending.id}이 결재 대기 중입니다.{" "}
          <Link to={`/approvals/${pending.id}`} className="cell-nowrap underline">
            승인 #{pending.id} 보기
          </Link>{" "}
          결재가 끝나면 다시 확정하세요.
        </p>
      )}
      {otherActive && requested === null && (
        <p className="mt-1 break-keep">
          진행 중인 승인이 있습니다.{" "}
          <Link to={`/approvals/${pending.id}`} className="cell-nowrap underline">
            승인 #{pending.id} 보기
          </Link>
        </p>
      )}
      {stale && (
        <p className="mt-1 break-keep text-signal-red">
          받은 승인 #{pending.id}은 수주 내용·금액이 바뀌어 더는 쓸 수 없습니다. 승인을 다시 요청해야 합니다.{" "}
          <Link to={`/approvals/${pending.id}`} className="cell-nowrap underline">
            승인 #{pending.id} 보기
          </Link>
        </p>
      )}
      {pending === null && requested === null && (
        <p className="mt-1 break-keep text-gray-700">여신 한도 초과는 승인(결재)으로만 해소됩니다. 승인을 요청하고 결재가 끝난 뒤 다시 확정하세요.</p>
      )}
      <p className="mt-1 break-keep text-xs text-gray-600">{SELF_DECISION_NOTICE} 다른 결재 자격자가 처리합니다.</p>
      {!waiting && !otherActive && (
        <button type="button" onClick={onRequest} className="cell-nowrap mt-2 rounded border border-gray-900 px-3 py-1">
          {stale ? "승인 다시 요청" : "승인 요청 올리기"}
        </button>
      )}
    </div>
  );
}

function ConfirmResultView({
  result,
  onReload,
  regionRef,
}: {
  result: ConfirmResult;
  onReload: () => void;
  regionRef: React.RefObject<HTMLDivElement | null>;
}) {
  const so = result.sales_order;
  // 서버가 준 확정 시점 결과에서 예외 승인·경고 사용분만 추려 보인다(판정은 서버 값 그대로).
  const overridden = result.gates.filter((g) => g.settlement === "OVERRIDDEN");
  const warned = result.gates.filter((g) => g.level === "WARN");
  return (
    <div
      ref={regionRef}
      tabIndex={-1}
      role="region"
      aria-label="확정 결과"
      className="mt-3 rounded border border-gray-900 p-3 text-sm focus:outline focus:outline-2 focus:outline-gray-900"
    >
      <p className="break-keep font-medium">수주 {so.doc_number}를 확정했습니다. 단가·환율·결제조건·라인은 이제 고칠 수 없습니다.</p>
      {so.qt_id !== null && (
        <p className="mt-1 break-keep text-gray-700">
          연결된 견적{" "}
          <Link to={`/quotations/${so.qt_id}`} className="cell-nowrap underline">
            {so.qt_doc_number ?? `#${so.qt_id}`}
          </Link>
          이(가) &apos;수주전환&apos;으로 표시됩니다.
        </p>
      )}
      {overridden.length > 0 && (
        <p className="mt-1 break-keep text-gray-700">예외 승인을 사용한 게이트: {overridden.map(gateTargetLabel).join(", ")}</p>
      )}
      {warned.length > 0 && <p className="mt-1 break-keep text-gray-700">경고 상태로 통과한 게이트: {warned.map(gateTargetLabel).join(", ")}</p>}
      <p className="mt-1 break-keep text-gray-700">
        {allocationLabel(result.allocation.status)}
        {HANGUL.test(result.allocation.note) && ` — ${result.allocation.note}`}
      </p>
      <button type="button" onClick={onReload} className="cell-nowrap mt-2 rounded border border-gray-300 px-3 py-1">
        최신 내용 불러오기
      </button>
    </div>
  );
}

function ConfirmEvidence({ so, confirmedAt }: { so: SalesOrderDetail; confirmedAt: string }) {
  return (
    <section aria-labelledby="confirm-evidence-title" className="mt-4 rounded border border-gray-200 p-3 text-sm">
      <h3 id="confirm-evidence-title" className="font-semibold">
        확정 증거 요약
      </h3>
      <p className="mt-1 break-keep text-xs text-gray-500">
        확정된 수주는 읽기 전용입니다. 단가·환율·결제조건·라인은 동결되어 고칠 수 없고, 바꿔야 하면 취소 후 새 수주를 만드세요. 아래는 확정 시점에 서버가 남긴 값입니다.
      </p>
      <dl className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <dt className="text-xs text-gray-500">확정 일시</dt>
          <dd className="cell-nowrap text-center">{toKstDisplay(confirmedAt)}</dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500">여신 판정</dt>
          <dd className="break-keep text-center">
            {creditVerdictLabel(so.credit_verdict)}
            {so.credit_approval_id != null && (
              <>
                {" "}
                <Link to={`/approvals/${so.credit_approval_id}`} className="cell-nowrap underline">
                  승인 #{so.credit_approval_id}
                </Link>
              </>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500">PI 입금 게이트 판정</dt>
          <dd className="break-keep text-center">{piVerdictLabel(so.pi_gate_verdict)}</dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500">확정 증거 번호</dt>
          <dd className="num text-center">{so.confirm_evaluation_id != null ? `#${so.confirm_evaluation_id}` : "—"}</dd>
        </div>
      </dl>
      <p className="mt-2 break-keep text-xs text-gray-600">
        예외 승인(override)·경고 사용의 상세는 확정 증거 번호의 기록에 남아 있습니다(이 화면은 판정 요약만 표시합니다).
      </p>
    </section>
  );
}
