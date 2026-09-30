// 확인 다이얼로그 (S3-1 G-01 — 발행·취소·개정 등 되돌릴 수 없는 전표 동작의 공통 확인창).
//
// ★ 위험 동작임을 제목·설명·버튼 문구로 명확히 한다(danger). 사유를 받는 경우 빈 사유는 제출할 수 없다.
// ★ 접근성: role=dialog·aria-modal·제목/설명 연결, 열리면 첫 입력·확인 버튼에 포커스, Esc로 닫기.

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

interface ConfirmDialogProps {
  title: string;
  description: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  /** 지정하면 사유 입력칸을 보이고 1자 이상을 요구한다. */
  reasonLabel?: string;
  reasonMaxLength?: number;
  pending?: boolean;
  error?: string | null;
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
  pending = false,
  error = null,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const titleId = useId();
  const descId = useId();
  const [reason, setReason] = useState("");
  const firstRef = useRef<HTMLTextAreaElement | HTMLButtonElement | null>(null);

  useEffect(() => {
    firstRef.current?.focus();
  }, []);

  const needsReason = reasonLabel !== undefined;
  const blocked = pending || (needsReason && reason.trim() === "");

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
        }}
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
          </label>
        )}
        {error && (
          <p role="alert" className="mt-3 break-keep text-sm text-signal-red">
            {error}
          </p>
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
