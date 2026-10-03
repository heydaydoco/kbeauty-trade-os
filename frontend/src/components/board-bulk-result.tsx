// 오더 보드 벌크 결과 모달 (S3-1 PR-15b — PR-15a 인계 화면 규칙 B7).
//
// ★ 건마다 서버 `message_ko`를 **먼저** 보인다(코드는 보조 표기). 분류(outcome)는 서버 값 그대로 — 화면이 재판정하지 않는다.
// ★ BLOCKED → 상세 화면 링크(인테이크 상세 / 수주 상세의 확정·게이트 패널). 벌크에는 예외 승인·승인 요청 버튼을 두지 않는다.
//   `blocked_gates`가 빈 BLOCKED는 업무 거부(품번 재해석·중복 PO 등) — '개별 처리 필요'와 code를 보인다.
// ★ CONFLICT → 보드 새로 고침 안내. FORBIDDEN·FAILED·SKIPPED·OK는 각자 다른 배지·문구.
// ★ 응답을 못 받은 경우(504·네트워크)만 '결과 다시 받기'(같은 키·같은 본문 재전송)를 보인다.

import { useRef } from "react";
import { Link } from "react-router";
import { gateLabel, levelLabel } from "../lib/gate";
import {
  ACTION_LABEL,
  OUTCOME_ORDER,
  detailPath,
  outcomeBadgeClass,
  outcomeLabel,
  type BulkAction,
  type BulkItemResult,
  type BulkReport,
  type CardKind,
} from "../lib/order-board";
import { useDialogBehavior } from "./confirm-dialog";

interface BulkResultDialogProps {
  action: BulkAction;
  report: BulkReport | null;
  /** 리포트를 못 받았을 때의 한국어 문구. */
  error: string | null;
  /** 응답 유실(504·네트워크) — 같은 키로 재전송 가능. */
  canResend: boolean;
  pending: boolean;
  /** 요청 당시 카드 표시 이름(`KIND:id` → ref_label). */
  labels: ReadonlyMap<string, string>;
  onResend: () => void;
  onRefreshBoard: () => void;
  onClose: () => void;
}

const fallbackLabel = (kind: CardKind, id: number): string => (kind === "INTAKE" ? `IN-${id}` : `수주 #${id}`);

function ResultRow({ row, label, onRefreshBoard }: { row: BulkItemResult; label: string; onRefreshBoard: () => void }) {
  const isBlocked = row.outcome === "BLOCKED";
  return (
    <li className="border-t border-gray-100 py-2" data-outcome={row.outcome}>
      {/* message_ko가 먼저 — 사람이 읽는 문장이 앞, 분류·코드는 보조. */}
      <p className="break-keep text-sm">{row.message_ko}</p>
      <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-600">
        <span className="cell-nowrap font-medium">{label}</span>
        <span className={`cell-nowrap rounded border px-1.5 py-0.5 ${outcomeBadgeClass(row.outcome)}`}>{outcomeLabel(row.outcome)}</span>
        {row.code && <span className="cell-nowrap font-mono text-gray-500">{row.code}</span>}
        {row.outcome === "OK" && row.doc_number && <span className="cell-nowrap">수주 {row.doc_number}</span>}
        {row.outcome === "OK" && row.sales_order_id !== null && row.kind === "INTAKE" && (
          <Link to={`/sales-orders/${row.sales_order_id}`} className="cell-nowrap underline">
            생성된 수주 보기
          </Link>
        )}
      </p>
      {isBlocked && (
        <div className="mt-1 text-xs">
          {row.blocked_gates.length === 0 ? (
            <p className="break-keep text-amber-800">
              개별 처리 필요 — 게이트 판정이 아니라 업무상 막힌 건입니다{row.code ? `(${row.code})` : ""}. 상세 화면에서 원인을 확인해 처리해 주세요.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {row.blocked_gates.map((gate, index) => (
                <li key={`${gate.gate_code}-${gate.line_id ?? "h"}-${index}`} className="break-keep">
                  <span className="cell-nowrap font-medium">
                    {gateLabel(gate.gate_code)}
                    {gate.line_no !== null ? ` · 라인 ${gate.line_no}` : ""} · {levelLabel(gate.level)}
                  </span>{" "}
                  {gate.message_ko}
                </li>
              ))}
            </ul>
          )}
          <Link to={detailPath(row.kind, row.id)} className="cell-nowrap mt-1 inline-block underline">
            {row.kind === "INTAKE" ? "인테이크 상세에서 처리" : "수주 상세(확정·게이트)에서 처리"}
          </Link>
        </div>
      )}
      {row.outcome === "CONFLICT" && (
        <p className="mt-1 break-keep text-xs text-blue-800">
          다른 사람이 먼저 바꾼 건입니다. 보드를 새로 고친 뒤 다시 선택해 주세요.{" "}
          <button type="button" onClick={onRefreshBoard} className="cell-nowrap underline">
            보드 새로 고침
          </button>
        </p>
      )}
      {row.outcome === "FORBIDDEN" && <p className="mt-1 break-keep text-xs text-signal-red">이 건을 처리할 권한이 없습니다.</p>}
    </li>
  );
}

export function BulkResultDialog({
  action,
  report,
  error,
  canResend,
  pending,
  labels,
  onResend,
  onRefreshBoard,
  onClose,
}: BulkResultDialogProps) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  useDialogBehavior(boxRef, () => {
    if (!pending) onClose();
  });
  const hasConflict = report?.results.some((row) => row.outcome === "CONFLICT") ?? false;

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4">
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-label={`${ACTION_LABEL[action]} 결과`}
        className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-lg bg-white p-5 shadow-lg"
      >
        <h2 className="text-lg font-bold">{ACTION_LABEL[action]} 결과</h2>
        {pending && <p className="mt-2 text-sm text-gray-500">처리 중… 건마다 따로 처리하므로 시간이 걸릴 수 있습니다.</p>}
        {error && (
          <p role="alert" className="mt-2 break-keep text-sm text-signal-red">
            {error}
          </p>
        )}
        {canResend && (
          <button
            type="button"
            disabled={pending}
            onClick={onResend}
            className="cell-nowrap mt-2 w-fit rounded bg-gray-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            결과 다시 받기
          </button>
        )}
        {report && (
          <>
            <p role="status" className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-sm">
              <span className="cell-nowrap">
                전체 <span className="num">{report.total}</span>건
              </span>
              {OUTCOME_ORDER.map((outcome) =>
                (report.outcome_counts[outcome] ?? 0) > 0 ? (
                  <span key={outcome} className="cell-nowrap">
                    {outcomeLabel(outcome)} <span className="num">{report.outcome_counts[outcome]}</span>
                  </span>
                ) : null,
              )}
            </p>
            {hasConflict && (
              <p className="mt-1 break-keep text-xs text-gray-600">경합 건이 있습니다 — 보드를 새로 고친 뒤 남은 건을 다시 선택해 처리해 주세요.</p>
            )}
            <ul aria-label="건별 결과" className="mt-2 overflow-auto">
              {report.results.map((row) => (
                <ResultRow
                  key={`${row.kind}:${row.id}`}
                  row={row}
                  label={labels.get(`${row.kind}:${row.id}`) ?? fallbackLabel(row.kind, row.id)}
                  onRefreshBoard={onRefreshBoard}
                />
              ))}
            </ul>
          </>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onRefreshBoard}
            disabled={pending}
            className="cell-nowrap rounded border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-50"
          >
            보드 새로 고침
          </button>
          <button
            type="button"
            onClick={onClose}
            disabled={pending}
            className="cell-nowrap rounded border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-50"
          >
            닫기
          </button>
        </div>
      </div>
    </div>
  );
}
