// 정책 설정 화면 (S3-1 PR-4 · design-E E8) — ADMIN 전용.
//
// ★ 화면 게이트는 표시일 뿐이다. GET·PUT 모두 서버가 ADMIN을 재검증한다(§18.1).
// ★ 미설정(UNSET_DEFAULT)은 "조용한 기본값"이 아니다 — 적색 배지+글자로 항상 드러낸다(색만으로 구분 금지).
// ★ 저장 키: (정책 key, version, 값, 사유) 조합이 바뀔 때만 새로 만들고, 같은 조합의 재클릭·재시도는
//   같은 Idempotency-Key를 쓴다(E9-4 — 본문이 바뀌었는데 키를 재사용하면 KEY_CONFLICT).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { ApiError, apiFetch } from "../lib/api";
import { toKstDisplay } from "../lib/datetime";
import { FRESH_EVERY_TIME, usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";

export type PolicyKind = "MODE" | "BASIS_POINTS";

export interface PolicyRow {
  key: string;
  kind: PolicyKind;
  label_ko: string;
  description_ko: string;
  allowed: string[];
  minimum: number | null;
  maximum: number | null;
  value: string | number;
  source: "SET" | "UNSET_DEFAULT";
  version: number | null;
  updated_by_name: string | null;
  updated_at: string | null;
}

export const POLICIES_QUERY_KEY = ["policies"] as const;

export const CONFLICT_MESSAGE =
  "다른 관리자가 먼저 수정했습니다. 새로 고침 후 다시 저장해 주세요.";

const MODE_LABEL: Record<string, string> = { OFF: "끔", WARN: "경고", BLOCK: "차단" };

export function modeLabel(code: string): string {
  return MODE_LABEL[code] ?? code;
}

/** bp → "5%" / "0.25%". 산술 없이 문자열로 끊는다(부동소수 오차·§2 ADR-02 환산 산술 금지와 같은 이유). */
export function bpToPercentText(bp: number): string {
  const sign = bp < 0 ? "-" : "";
  const digits = String(Math.abs(bp)).padStart(3, "0");
  const whole = digits.slice(0, -2);
  const frac = digits.slice(-2).replace(/0+$/, "");
  return frac === "" ? `${sign}${whole}%` : `${sign}${whole}.${frac}%`;
}

/** "5" / "0.25" (% 입력) → bp 정수. 소수 둘째 자리까지만 — 아니면 null. */
export function percentToBp(text: string): number | null {
  const trimmed = text.trim();
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(trimmed);
  if (!match) return null;
  const frac = (match[2] ?? "").padEnd(2, "0");
  return Number(match[1]) * 100 + Number(frac);
}

/** 상한 문구 — 서버가 준 minimum/maximum에서만 만든다(화면에 숫자를 박지 않는다). */
export function rangeText(row: PolicyRow): string {
  const min = row.minimum ?? 0;
  if (row.maximum === null) return `${bpToPercentText(min)}(${min}bp) 이상`;
  return `${bpToPercentText(min)}(${min}bp) ~ ${bpToPercentText(row.maximum)}(${row.maximum}bp)`;
}

export function displayValue(row: PolicyRow): string {
  if (row.kind === "MODE") return modeLabel(String(row.value));
  const bp = Number(row.value);
  return `${bpToPercentText(bp)} (${bp}bp)`;
}

function defaultBehaviorText(kind: PolicyKind): string {
  return kind === "MODE"
    ? "미설정 — 기본 동작(차단)"
    : "미설정 — 기본값 0bp(편차 불허)";
}

function SourceBadge({ row }: { row: PolicyRow }) {
  if (row.source === "SET") {
    return (
      <span className="cell-nowrap rounded bg-green-100 px-1.5 py-0.5 text-xs font-semibold text-green-800">
        설정됨
      </span>
    );
  }
  return (
    <span className="cell-nowrap rounded bg-red-100 px-1.5 py-0.5 text-xs font-semibold text-red-800">
      {defaultBehaviorText(row.kind)}
    </span>
  );
}

const REASON_MIN = 2;
const REASON_MAX = 300;

function EditPanel({ row, onDone }: { row: PolicyRow; onDone: (saved: boolean) => void }) {
  const client = useQueryClient();
  const [modeValue, setModeValue] = useState(String(row.value));
  const [percent, setPercent] = useState(
    row.kind === "BASIS_POINTS" ? bpToPercentText(Number(row.value)).replace("%", "") : "",
  );
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const keyRef = useRef<{ signature: string; key: string } | null>(null);

  const bp = row.kind === "BASIS_POINTS" ? percentToBp(percent) : null;
  const min = row.minimum ?? 0;
  const bpProblem =
    row.kind !== "BASIS_POINTS"
      ? null
      : bp === null
        ? "숫자(소수 둘째 자리까지)로 입력해 주세요."
        : bp < min || (row.maximum !== null && bp > row.maximum)
          ? `허용 범위를 벗어났습니다 — ${rangeText(row)}`
          : null;
  const value: string | number | null = row.kind === "MODE" ? modeValue : bp;
  const reasonTrimmed = reason.trim();
  const reasonOk = reasonTrimmed.length >= REASON_MIN && reasonTrimmed.length <= REASON_MAX;

  const save = useMutation({
    mutationFn: () => {
      const signature = JSON.stringify([row.key, row.version, value, reasonTrimmed]);
      if (keyRef.current?.signature !== signature) {
        keyRef.current = { signature, key: crypto.randomUUID() };
      }
      return apiFetch(`/v1/policies/${encodeURIComponent(row.key)}`, {
        method: "PUT",
        body: { value, version: row.version, reason: reasonTrimmed },
        idempotencyKey: keyRef.current.key,
      });
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: POLICIES_QUERY_KEY });
      onDone(true);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        setNotice(CONFLICT_MESSAGE);
        void client.invalidateQueries({ queryKey: POLICIES_QUERY_KEY });
      }
    },
  });

  const errorMessage =
    notice ?? (save.error ? (save.error instanceof ApiError ? save.error.message : "저장하지 못했습니다.") : null);
  const canSave = value !== null && bpProblem === null && reasonOk && !save.isPending;

  return (
    <form
      className="flex flex-col gap-3 text-sm"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSave) save.mutate();
      }}
    >
      {row.kind === "MODE" ? (
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">모드</span>
          <select
            className="w-40 rounded border border-gray-300 px-2 py-1"
            value={modeValue}
            onChange={(event) => setModeValue(event.target.value)}
          >
            {row.allowed.map((code) => (
              <option key={code} value={code}>
                {modeLabel(code)}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <div className="flex flex-col gap-1">
          <label className="flex flex-col gap-1">
            <span className="cell-nowrap text-gray-600">허용치 (%)</span>
            <input
              className="num w-40 rounded border border-gray-300 px-2 py-1"
              inputMode="decimal"
              value={percent}
              onChange={(event) => setPercent(event.target.value)}
            />
          </label>
          <span className="text-gray-500">
            {bp !== null ? `= ${bp}bp` : "= -"} · 허용 범위 {rangeText(row)}
          </span>
          {bpProblem && (
            <span role="alert" className="text-red-700">
              {bpProblem}
            </span>
          )}
        </div>
      )}

      <label className="flex flex-col gap-1">
        <span className="text-gray-600">
          변경 사유 ({REASON_MIN}~{REASON_MAX}자, 필수)
        </span>
        <textarea
          className="rounded border border-gray-300 px-2 py-1"
          rows={2}
          maxLength={REASON_MAX}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </label>

      {errorMessage && (
        <p role="alert" className="text-red-700">
          {errorMessage}
        </p>
      )}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={!canSave}
          className="cell-nowrap rounded bg-gray-900 px-3 py-1 text-white disabled:opacity-40"
        >
          저장
        </button>
        <button type="button" onClick={() => onDone(false)} className="cell-nowrap rounded border border-gray-300 px-3 py-1">
          취소
        </button>
      </div>
    </form>
  );
}

export function PoliciesPage() {
  const { me } = useSession();
  const isAdmin = hasRole(me);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const policies = usePagedQuery<PolicyRow>(
    POLICIES_QUERY_KEY,
    "/v1/policies",
    isAdmin,
    FRESH_EVERY_TIME,
  );

  if (!isAdmin) {
    return (
      <section>
        <h1 className="text-2xl font-bold">정책 설정</h1>
        <p role="alert" className="mt-4 text-red-700">
          접근할 수 없습니다 — 정책 설정은 관리자만 볼 수 있습니다.
        </p>
      </section>
    );
  }

  const rows = policies.data?.items ?? [];

  return (
    <section>
      <header>
        <h1 className="text-2xl font-bold">정책 설정</h1>
        <p className="mt-1 text-sm text-gray-500">
          미설정 정책은 가장 엄격한 기본 동작으로 처리 중입니다. 관리자가 저장하기 전까지 유지됩니다.
        </p>
      </header>

      {saved && <p className="mt-3 text-sm text-green-700">저장했습니다.</p>}
      {policies.isPending && <p className="mt-4 text-gray-500">불러오는 중…</p>}
      {policies.error && (
        <p role="alert" className="mt-4 text-red-700">
          {policies.error instanceof ApiError
            ? policies.error.message
            : "정책 목록을 불러오지 못했습니다."}
        </p>
      )}

      {policies.data && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-left text-gray-600">
                <th className="cell-nowrap p-2">정책</th>
                <th className="cell-nowrap p-2">설명</th>
                <th className="cell-nowrap p-2 text-center">현재값</th>
                <th className="cell-nowrap p-2">출처</th>
                <th className="cell-nowrap p-2">마지막 변경자</th>
                <th className="cell-nowrap p-2">변경 시각</th>
                <th className="cell-nowrap p-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <PolicyTableRow
                  key={row.key}
                  row={row}
                  editing={editingKey === row.key}
                  onEdit={() => {
                    setSaved(false);
                    setEditingKey(row.key);
                  }}
                  onDone={(didSave) => {
                    setEditingKey(null);
                    setSaved(didSave);
                  }}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function PolicyTableRow({
  row,
  editing,
  onEdit,
  onDone,
}: {
  row: PolicyRow;
  editing: boolean;
  onEdit: () => void;
  onDone: (saved: boolean) => void;
}) {
  return (
    <>
      <tr className="border-b border-gray-100 align-top">
        <td className="p-2 font-semibold">{row.label_ko}</td>
        <td className="p-2 text-gray-600">{row.description_ko}</td>
        <td className="num cell-nowrap p-2">{displayValue(row)}</td>
        <td className="p-2">
          <SourceBadge row={row} />
        </td>
        <td className="cell-nowrap p-2">{row.updated_by_name ?? "-"}</td>
        <td className="cell-nowrap p-2">{row.updated_at ? toKstDisplay(row.updated_at) : "-"}</td>
        <td className="p-2">
          <button
            type="button"
            onClick={onEdit}
            aria-label={`${row.label_ko} 편집`}
            className="cell-nowrap rounded border border-gray-300 px-2 py-1"
          >
            편집
          </button>
        </td>
      </tr>
      {editing && (
        <tr className="border-b border-gray-100 bg-gray-50">
          <td colSpan={7} className="p-3">
            <EditPanel row={row} onDone={onDone} />
          </td>
        </tr>
      )}
    </>
  );
}
