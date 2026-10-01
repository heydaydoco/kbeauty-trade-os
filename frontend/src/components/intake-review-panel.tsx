// 인테이크 검토 조치 — 품번 다시 확인(resolve)·거부·접수 확정 (S3-1 PR-13b — design-D D5·D7 / ADR-0071).
//
// 규칙(서버가 정본, 화면은 편의 — §18.1):
// - ★ 프런트는 확정 가능 여부를 다시 판정하지 않는다. 확정 버튼은 대기 상태+쓰기 역할이면 `intake_confirmable`이 false여도 항상 서버를 부른다 —
//   서버의 422(UNMAPPED_ITEMS·DUPLICATE_SKU·확정 시점 납기 경과 등)·409(STALE_MAPPING·중복 PO·NOT_PENDING·복제 원본 자격 상실 등)를 한국어로 안내한다.
// - 멱등 키: 확정·품번 재해석은 (인테이크 id, version)당 1개 — 본문 `{id, version}` 기준 Map으로 재사용(같은 version 재시도=같은 키, version이 오르면 새 키), 성공하면 비운다.
//   거부는 본문(version+사유)당 1개. 더블클릭 잠금(ref)은 mutationFn의 finally에서 푼다. 요청 중 Esc/닫기는 무시한다. `networkMode:"always"`.
// - 다이얼로그는 컴포넌트 상태다 — 창 포커스 재조회로 데이터가 바뀌어도 닫히거나 입력(사유)이 지워지지 않는다.
// - 확정·거부 뒤 기준 version(상위 `version`)은 자동으로 옮기지 않는다 — '최신 내용 불러오기'(상위 onReload)로만. 재해석 결과는 화면에 그대로 보이므로 상위가 응답 version으로 옮긴다.
// - 성공 뒤 결과 영역으로 포커스를 옮기고 role=status live region으로 알린다.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../lib/api";
import { keyFor } from "../lib/confirm";
import { newGateKey, validateOverrideReason } from "../lib/gate";
import { DOCUMENT_FLOW_QUERY_KEY, SALES_ORDERS_QUERY_KEY } from "../lib/sales-order";
import {
  ORDER_INTAKES_QUERY_KEY,
  intakeErrorMessage,
  isIntakeRecoverable,
  isWriteForbidden,
  orderIntakeDetailKey,
  orderIntakeGatesKey,
  type IntakeConfirmOut,
  type IntakeDetail,
} from "../lib/order-intake";
import { ConfirmDialog } from "./confirm-dialog";

type DialogKind = "confirm" | "reject";
type FocusWhich = "result" | "note";

export function IntakeReviewPanel({
  intake,
  version,
  canWrite,
  onReload,
  reloadToken = 0,
  onResolved,
  onFinished,
}: {
  intake: IntakeDetail;
  /** 쓰기에 싣는 기준 version(화면이 본 값). */
  version: number;
  canWrite: boolean;
  onReload: () => void;
  /** 상위가 '최신 내용 불러오기'를 할 때마다 올린다 — 로컬 오류·403 래치를 비운다. */
  reloadToken?: number;
  /** 품번 다시 확인 성공 — 상위가 응답을 화면 상태·기준 version으로 반영한다. */
  onResolved: (next: IntakeDetail, prevVersion: number) => void;
  /** 확정·거부가 이 화면에서 끝났다(상위 stale 배너를 처리 맥락으로). */
  onFinished: () => void;
}) {
  const client = useQueryClient();
  const [forbidden, setForbidden] = useState(false);
  const [dialog, setDialog] = useState<DialogKind | null>(null);
  const [confirmed, setConfirmed] = useState<IntakeConfirmOut | null>(null);
  const [rejected, setRejected] = useState(false);
  const [announce, setAnnounce] = useState<{ text: string; n: number } | null>(null);
  const pending = intake.status === "PENDING";

  const resultRef = useRef<HTMLDivElement | null>(null);
  const noteRef = useRef<HTMLParagraphElement | null>(null);
  const [focusTarget, setFocusTarget] = useState<{ which: FocusWhich; n: number } | null>(null);
  useEffect(() => {
    if (focusTarget === null) return;
    ({ result: resultRef, note: noteRef })[focusTarget.which].current?.focus();
  }, [focusTarget]);
  const focusOn = (which: FocusWhich) => setFocusTarget((prev) => ({ which, n: (prev?.n ?? 0) + 1 }));
  const say = (text: string) => setAnnounce((prev) => ({ text, n: (prev?.n ?? 0) + 1 }));

  const confirmKeys = useRef(new Map<string, string>());
  const resolveKeys = useRef(new Map<string, string>());
  const rejectKeys = useRef(new Map<string, string>());
  const confirmLock = useRef(false);
  const resolveLock = useRef(false);
  const rejectLock = useRef(false);

  const seen = useRef({ version, token: reloadToken });
  useEffect(() => {
    const prev = seen.current;
    if (prev.version === version && prev.token === reloadToken) return;
    seen.current = { version, token: reloadToken };
    setForbidden(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, reloadToken]);

  /** 쓰기 뒤에 서버가 다시 계산하는 값을 새로 읽는다 — 상세는 기준 version을 옮기지 않고 데이터만 갱신된다. */
  function refreshAfterFailure() {
    void client.invalidateQueries({ queryKey: orderIntakeGatesKey(intake.id) });
    void client.invalidateQueries({ queryKey: orderIntakeDetailKey(intake.id) });
  }

  const resolve = useMutation({
    networkMode: "always",
    mutationFn: async (input: { version: number; key: string }) => {
      try {
        return await apiFetch<IntakeDetail>(`/v1/order-intakes/${intake.id}/resolve`, {
          method: "POST",
          idempotencyKey: input.key,
          body: { version: input.version },
        });
      } finally {
        resolveLock.current = false;
      }
    },
    onSuccess: (next, input) => {
      resolveKeys.current.clear();
      client.setQueryData(orderIntakeDetailKey(intake.id), next);
      void client.invalidateQueries({ queryKey: orderIntakeGatesKey(intake.id) });
      void client.invalidateQueries({ queryKey: [...ORDER_INTAKES_QUERY_KEY, "list"] });
      onResolved(next, input.version);
      // 서버는 바뀐 것이 있을 때만 version을 올린다 — 올랐는지만 본다(어느 라인이 어떻게 바뀌었는지는 아래 라인 표가 서버 값으로 보여 준다).
      say(next.version === input.version ? "품번을 다시 확인했습니다. 바뀐 해석이 없습니다." : "품번을 다시 확인했습니다. 바뀐 해석을 반영했으니 라인의 매핑 상태를 검토하세요.");
    },
    onError: (error) => {
      if (isWriteForbidden(error)) setForbidden(true);
      else refreshAfterFailure();
    },
  });

  const confirm = useMutation({
    networkMode: "always",
    mutationFn: async (input: { version: number; key: string }) => {
      try {
        return await apiFetch<IntakeConfirmOut>(`/v1/order-intakes/${intake.id}/confirm`, {
          method: "POST",
          idempotencyKey: input.key,
          body: { version: input.version },
        });
      } finally {
        confirmLock.current = false;
      }
    },
    onSuccess: (out) => {
      setDialog(null);
      setConfirmed(out);
      confirmKeys.current.clear();
      client.setQueryData(orderIntakeDetailKey(intake.id), out.intake);
      say(`접수를 확정했습니다. 수주 ${out.doc_number}이(가) 접수 상태로 만들어졌습니다.`);
      focusOn("result");
      onFinished();
      // 확정은 인테이크(목록·상세·게이트)·수주(목록)·문서 흐름을 바꾼다.
      void client.invalidateQueries({ queryKey: ORDER_INTAKES_QUERY_KEY });
      void client.invalidateQueries({ queryKey: SALES_ORDERS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: DOCUMENT_FLOW_QUERY_KEY });
    },
    onError: (error) => {
      if (isWriteForbidden(error)) {
        setForbidden(true);
        setDialog(null);
        focusOn("note");
        return;
      }
      // 실패(422·409)는 다이얼로그 안에 한국어로 안내하고, 서버가 다시 계산하는 매핑 상태·게이트를 새로 읽는다.
      refreshAfterFailure();
    },
  });

  const reject = useMutation({
    networkMode: "always",
    mutationFn: async (input: { version: number; reason: string; key: string }) => {
      try {
        return await apiFetch<IntakeDetail>(`/v1/order-intakes/${intake.id}/reject`, {
          method: "POST",
          idempotencyKey: input.key,
          body: { version: input.version, reason: input.reason },
        });
      } finally {
        rejectLock.current = false;
      }
    },
    onSuccess: (next) => {
      setDialog(null);
      setRejected(true);
      rejectKeys.current.clear();
      client.setQueryData(orderIntakeDetailKey(intake.id), next);
      say("인테이크를 거부했습니다. 사유와 함께 보존됩니다.");
      focusOn("result");
      onFinished();
      void client.invalidateQueries({ queryKey: ORDER_INTAKES_QUERY_KEY });
    },
    onError: (error) => {
      if (isWriteForbidden(error)) {
        setForbidden(true);
        setDialog(null);
        focusOn("note");
        return;
      }
      refreshAfterFailure();
    },
  });

  function submitResolve() {
    if (resolveLock.current) return;
    const key = keyFor(resolveKeys.current, JSON.stringify({ id: intake.id, version }), newGateKey);
    resolveLock.current = true;
    resolve.mutate({ version, key });
  }

  function submitConfirm() {
    if (confirmLock.current) return;
    // (인테이크 id, version)당 1개 — 같은 version 재시도는 같은 키, version이 오르면 새 키.
    const key = keyFor(confirmKeys.current, JSON.stringify({ id: intake.id, version }), newGateKey);
    confirmLock.current = true;
    confirm.mutate({ version, key });
  }

  function submitReject(reason: string) {
    if (rejectLock.current) return;
    const key = keyFor(rejectKeys.current, JSON.stringify({ id: intake.id, version, reason }), newGateKey);
    rejectLock.current = true;
    reject.mutate({ version, reason, key });
  }

  /** 충돌 안내의 '최신 내용 불러오기' — 다이얼로그·오류를 비우고 상위가 기준 version·상세를 다시 읽게 한다. */
  function reload() {
    confirm.reset();
    reject.reset();
    resolve.reset();
    setDialog(null);
    setForbidden(false);
    onReload();
  }

  function openDialog(kind: DialogKind) {
    confirm.reset();
    reject.reset();
    setDialog(kind);
  }

  const write = canWrite && !forbidden;
  // 남이 처리해 대기가 아니게 되어도 요청 중·오류 표시 중에는 다이얼로그를 닫지 않는다 — 입력·오류 안내가 사라지지 않게(서버가 최종).
  const confirmOpen = dialog === "confirm" && (pending || confirm.isPending || confirm.isError);
  const rejectOpen = dialog === "reject" && (pending || reject.isPending || reject.isError);
  const justFinished = confirmed !== null || rejected;
  const showActions = pending && !justFinished;

  return (
    <section aria-labelledby="intake-review-title" className="rounded-lg border border-gray-200 p-4">
      <h2 id="intake-review-title" className="text-lg font-semibold">
        검토·확정
      </h2>

      <p role="status" aria-label="처리 결과" className={announce ? "mt-2 break-keep text-sm font-medium" : "sr-only"}>
        {/* 같은 문구가 연속으로 나와도 다시 읽히도록 호출마다 노드를 새로 만든다. */}
        <span key={announce?.n ?? 0}>{announce?.text ?? ""}</span>
      </p>

      {confirmed !== null && (
        <div ref={resultRef} tabIndex={-1} role="region" aria-label="접수 확정 결과" className="mt-3 rounded border border-gray-900 p-3 text-sm focus:outline focus:outline-2 focus:outline-gray-900">
          <p className="break-keep font-medium">접수를 확정했습니다.</p>
          <p className="mt-1 break-keep">
            수주{" "}
            <Link to={`/sales-orders/${confirmed.sales_order_id}`} className="underline">
              {confirmed.doc_number}
            </Link>
            이(가) 접수 상태로 만들어졌습니다. 이 인테이크는 종결되어 더 고칠 수 없습니다. 이어서 수주 상세에서 게이트를 확인하고 수주를 확정하세요.
          </p>
        </div>
      )}
      {rejected && (
        <div ref={resultRef} tabIndex={-1} role="region" aria-label="거부 결과" className="mt-3 rounded border border-gray-400 p-3 text-sm focus:outline focus:outline-2 focus:outline-gray-900">
          <p className="break-keep font-medium">인테이크를 거부했습니다. 행·원본·사유는 영구 보존되며 더 고칠 수 없습니다. 같은 PO번호는 다시 접수할 수 있습니다.</p>
        </div>
      )}

      {showActions && write && (
        <div className="mt-3 grid gap-3">
          <p className="break-keep text-sm text-gray-700">
            확정하면 수주(접수 상태)가 만들어지고 이 인테이크는 종결됩니다. 확정 가능 여부는 서버가 확정할 때 최종 판정합니다(위 게이트 요약은 참고용 사전 점검입니다).
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={submitResolve}
              disabled={resolve.isPending}
              className="cell-nowrap rounded border border-gray-900 px-4 py-2 text-sm disabled:opacity-50"
            >
              {resolve.isPending ? "확인 중…" : "품번 다시 확인"}
            </button>
            <button type="button" onClick={() => openDialog("confirm")} className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white">
              접수 확정
            </button>
            <button type="button" onClick={() => openDialog("reject")} className="cell-nowrap rounded border border-signal-red px-4 py-2 text-sm text-signal-red">
              거부
            </button>
          </div>
          {resolve.isError && (
            <div role="alert" className="break-keep text-sm text-signal-red">
              <p>{intakeErrorMessage(resolve.error, "resolve")}</p>
              {isIntakeRecoverable(resolve.error) && (
                <button type="button" onClick={reload} className="cell-nowrap mt-2 rounded border border-gray-300 px-3 py-1 text-gray-900">
                  최신 내용 불러오기
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {showActions && !write && (
        <p ref={noteRef} tabIndex={-1} role="note" className="mt-3 break-keep text-sm text-gray-600 focus:outline focus:outline-2 focus:outline-gray-900">
          품번 재해석·확정·거부는 무역·관리자만 할 수 있습니다. 이 화면에서는 조회만 할 수 있습니다.
        </p>
      )}

      {!pending && !justFinished && (
        <p role="note" className="mt-3 break-keep text-sm text-gray-600">
          {intake.status === "CONFIRMED" ? "이미 확정된 인테이크라 읽기 전용입니다." : "거부된 인테이크라 읽기 전용입니다."}
        </p>
      )}

      {confirmOpen && (
        <ConfirmDialog
          title="접수를 확정할까요?"
          danger
          confirmLabel="접수 확정"
          description={
            <div className="grid gap-2">
              <p>
                <strong>확정하면 되돌릴 수 없습니다.</strong>
              </p>
              <ul className="list-disc pl-5">
                <li>수주(접수 상태)가 새로 만들어지고 이 인테이크는 &apos;확정&apos;으로 종결됩니다. 이후 인테이크는 고칠 수 없습니다.</li>
                <li>서버가 품번 매핑·중복 바이어 PO·입력 완결성·요청납기 등을 다시 검사합니다. 하나라도 걸리면 수주는 만들어지지 않으며, 관리자도 우회할 수 없습니다.</li>
                <li>같은 요청을 다시 보내도 수주가 중복으로 만들어지지 않습니다.</li>
              </ul>
            </div>
          }
          pending={confirm.isPending}
          error={confirm.error ? intakeErrorMessage(confirm.error, "confirm") : null}
          onReload={isIntakeRecoverable(confirm.error) ? reload : undefined}
          onCancel={() => {
            // 요청 중 닫기(Esc)는 무시 — 닫고 reset하면 응답을 못 본 채 같은 키로 재시도하게 된다.
            if (confirm.isPending) return;
            confirm.reset();
            setDialog(null);
          }}
          onConfirm={submitConfirm}
        />
      )}

      {rejectOpen && (
        <ConfirmDialog
          title="인테이크를 거부할까요?"
          danger
          confirmLabel="거부"
          description={
            <div className="grid gap-2">
              <p>
                <strong>거부하면 되돌릴 수 없습니다.</strong>
              </p>
              <ul className="list-disc pl-5">
                <li>거부는 종결이며 행·원본·사유는 영구 보존됩니다(삭제 없음). 같은 PO번호는 다시 접수할 수 있습니다.</li>
                <li>바이어·통화를 잘못 골랐다면 거부한 뒤 다시 등록하세요.</li>
              </ul>
            </div>
          }
          reasonLabel="거부 사유 (5~500자)"
          reasonMinLength={5}
          reasonMaxLength={1000}
          reasonValidator={validateOverrideReason}
          pending={reject.isPending}
          error={reject.error ? intakeErrorMessage(reject.error, "reject") : null}
          onReload={isIntakeRecoverable(reject.error) ? reload : undefined}
          onCancel={() => {
            if (reject.isPending) return;
            reject.reset();
            setDialog(null);
          }}
          onConfirm={submitReject}
        />
      )}
    </section>
  );
}
