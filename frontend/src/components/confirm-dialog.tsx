// 확인 다이얼로그 (S3-1 G-01 — 발행·취소·개정 등 되돌릴 수 없는 전표 동작의 공통 확인창).
//
// ★ 위험 동작임을 제목·설명·버튼 문구로 명확히 한다(danger). 사유를 받는 경우 빈 사유는 제출할 수 없다.
// ★ 접근성: role=dialog·aria-modal·제목/설명 연결, 열리면 첫 입력·확인 버튼에 포커스, Esc로 닫기.

import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * 모달 공통 동작 — Tab 포커스 트랩, Esc(문서 레벨), 닫힐 때 연 버튼으로 포커스 복귀, 초기 포커스.
 * ★ Esc를 창이 아니라 문서에서 받는 이유: 포커스가 다이얼로그 밖(오버레이 뒤)에 있어도 닫히게 하려는 것이다.
 *   (SearchSelect 목록이 열려 있을 때는 그쪽이 Esc를 먼저 삼킨다.)
 */
export function useDialogBehavior(
  ref: RefObject<HTMLElement | null>,
  onEscape: () => void,
  initialFocus?: RefObject<HTMLElement | null>,
) {
  const escape = useRef(onEscape);
  escape.current = onEscape;

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const container = ref.current;
    if (container && !container.contains(document.activeElement)) {
      (initialFocus?.current ?? container.querySelector<HTMLElement>(FOCUSABLE))?.focus();
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        escape.current();
        return;
      }
      if (event.key !== "Tab" || !container) return;
      const items = Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE));
      const first = items[0];
      const last = items[items.length - 1];
      if (first === undefined || last === undefined) return;
      const active = document.activeElement;
      if (!container.contains(active)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (opener && document.contains(opener)) opener.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

interface ConfirmDialogProps {
  title: string;
  description: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  /** 지정하면 사유 입력칸을 보이고 1자 이상을 요구한다. */
  reasonLabel?: string;
  reasonMaxLength?: number;
  /** 사유 입력칸 바로 아래 안내(예: 자유 텍스트에 원가를 적지 말라는 경고 — ADR-0057). */
  reasonHint?: ReactNode;
  pending?: boolean;
  error?: string | null;
  /** 낙관 잠금 충돌(409)일 때 다이얼로그 안에 '최신 내용 불러오기'를 둔다 — 누르면 호출(보통 닫고 재조회). */
  onReload?: () => void;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  title,
  description,
  confirmLabel,
  danger = false,
  reasonLabel,
  reasonMaxLength = 500,
  reasonHint,
  pending = false,
  error = null,
  onReload,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const titleId = useId();
  const descId = useId();
  const [reason, setReason] = useState("");
  const firstRef = useRef<HTMLTextAreaElement | HTMLButtonElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  useDialogBehavior(boxRef, onCancel, firstRef);

  const needsReason = reasonLabel !== undefined;
  const blocked = pending || (needsReason && reason.trim() === "");

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4">
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        className="w-full max-w-md rounded-lg bg-white p-5 shadow-lg"
      >
        <h2 id={titleId} className={`text-lg font-bold ${danger ? "text-signal-red" : ""}`}>
          {danger ? "⚠ " : ""}
          {title}
        </h2>
        <div id={descId} className="mt-2 break-keep text-sm text-gray-700">
          {description}
        </div>
        {needsReason && (
          <label className="mt-4 flex flex-col gap-1 text-sm">
            <span className="text-gray-600">{reasonLabel}</span>
            <textarea
              ref={(node) => {
                firstRef.current = node;
              }}
              value={reason}
              maxLength={reasonMaxLength}
              rows={3}
              onChange={(event) => setReason(event.target.value)}
              className="rounded border border-gray-300 px-3 py-2"
            />
            {reasonHint && <span className="break-keep text-xs text-gray-500">{reasonHint}</span>}
          </label>
        )}
        {error && (
          <p role="alert" className="mt-3 break-keep text-sm text-signal-red">
            {error}
          </p>
        )}
        {error && onReload && (
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
            ref={(node) => {
              if (!needsReason) firstRef.current = node;
            }}
            disabled={blocked}
            onClick={() => onConfirm(reason.trim())}
            className={`cell-nowrap rounded px-4 py-2 text-sm text-white disabled:opacity-50 ${
              danger ? "bg-signal-red" : "bg-gray-900"
            }`}
          >
            {pending ? "처리 중…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
