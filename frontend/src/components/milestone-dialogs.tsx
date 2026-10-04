// 마일스톤 대화상자 — 계획 입력·변경(롤오버, 사유 필수)·실적 입력·정정(사유 필수)·통보 기록 (S3-2 PR-4b — design-D D6 / PROGRESS 'S3-2 PR-4a'·'PR-4c' 인계).
//
// 규칙(PR-3b 적대 검토 교훈을 처음부터):
// - 대화상자 1회 열림 = 멱등 키 1개(같은 본문 재시도 = 같은 키, 본문이 바뀌면 새 키). 더블클릭은 동기 잠금(ref) — 잠금 해제는 요청 finally.
// - 처리 중에는 입력 전체 잠금(<fieldset disabled>)·닫기 비활성·Esc 무시(응답 전 닫으면 결과를 못 보고 재오픈 시 새 키가 된다).
// - 기준 version은 **연 순간의 행**으로 고정한다(뒤에서 보드가 새로 와도 점프하지 않는다 — 겹친 편집은 서버 409 → '최신 내용 불러오기').
// - 결과를 모르는 실패(0·5xx)는 같은 키 재시도를 안내한다(새로 기록하지 않는다).
// - 사유가 필요한 경우(기존 계획 변경 = 롤오버, 기존 실적 정정·지우기)는 빈칸으로 제출할 수 없다.
// - 롤오버 + '통보도 지금 기록'은 요청 2회(계획 → 응답의 change.id로 통보) — 각자 키. 통보 실패는 롤오버를 되돌리지 않는다(design-D D6).
// - 날짜형은 문자열 그대로('YYYY-MM-DD'), 시각형은 벽시계 + IANA 시간대 → lib/datetime 변환(이 파일은 시각 객체를 만들지 않는다).

import { useMutation } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";
import { useDialogBehavior } from "./confirm-dialog";
import { SearchSelect } from "./search-select";
import { MilestoneValueText } from "./milestone-timeline";
import { apiFetch } from "../lib/api";
import { errorMessage } from "../lib/api-errors";
import { timeZoneChoices, toZonedPairDisplay, utcToZonedWallTime, zonedWallTimeToUtc } from "../lib/datetime";
import { partnerTypeLabel } from "../lib/labels";
import {
  DATETIME_MAX,
  DATETIME_MIN,
  DATE_MAX,
  DATE_MIN,
  NOTICE_PARTNER_TYPES,
  ROLLOVER_TYPES,
  actualBody,
  changeKindLabel,
  isInstant,
  milestoneTypeLabel,
  needsBoardReload,
  planBody,
  type InstantValue,
  type MilestoneBoard,
  type MilestoneChange,
  type MilestoneRow,
  type MilestoneWriteResult,
} from "../lib/milestone";
import { createKeyKeeper, isResultUnknown } from "../lib/shipment";
import type { Partner } from "../routes/partners";
import type { MilestoneEditMode } from "./milestone-timeline";

const inputClass = "rounded border border-gray-300 px-3 py-2 text-sm";
const REASON_MAX = 500;
const SUMMARY_MAX = 2000;
const DATE_SHAPE = /^\d{4}-\d{2}-\d{2}$/;

/** 'YYYY-MM-DD' 형식 + 업무 범위 안(문자열 비교 — 시각 객체 변환 0). */
const dateInRange = (value: string, max = DATE_MAX): boolean => DATE_SHAPE.test(value) && value >= DATE_MIN && value <= max;

/** 오류 문구 — 서버 한국어 그대로(낙관 잠금은 대상 안내문), 결과 모르는 실패는 같은 키 재시도 안내를 덧붙인다. */
export function writeErrorText(error: unknown, noun: string): string {
  const base = errorMessage(error, "요청을 처리하지 못했습니다.", noun);
  return isResultUnknown(error)
    ? `${base} 응답을 받지 못해 기록 여부를 알 수 없습니다. 같은 내용으로 한 번 더 누르면 같은 요청(같은 키)으로 결과를 확인합니다 — 새로 기록하지 않습니다.`
    : base;
}

// ── 대화상자 껍데기 ──

export function DialogShell({
  title,
  titleRef,
  pending,
  onClose,
  initialFocus,
  children,
}: {
  title: string;
  titleRef: RefObject<HTMLHeadingElement | null>;
  pending: boolean;
  onClose: () => void;
  initialFocus?: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const titleId = useId();
  const boxRef = useRef<HTMLDivElement | null>(null);
  // 처리 중 Esc는 무시한다(닫기 버튼과 같은 규칙).
  useDialogBehavior(
    boxRef,
    () => {
      if (!pending) onClose();
    },
    initialFocus,
  );
  // 바깥 p-4가 폭을 화면 − 2rem으로 묶는다(390px에서 대화상자 358px — design-D D13).
  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4">
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg bg-white p-5 shadow-lg"
      >
        <h2 id={titleId} ref={titleRef} tabIndex={-1} className="break-keep text-lg font-bold">
          {title}
        </h2>
        {children}
      </div>
    </div>
  );
}

// ── 통보 입력칸(롤오버 대화상자 안·변경 이력의 '통보 기록' 대화상자 공용) ──

export interface NoticeDraft {
  occurredOn: string;
  partnerType: string;
  partner: Partner | null;
  summary: string;
}

export const emptyNotice = (todayKst: string): NoticeDraft => ({ occurredOn: todayKst, partnerType: "", partner: null, summary: "" });

/** 통보 입력 문제 — 요지 필수(1~2000), 오간 날 = 업무 범위 안·서버 KST 오늘 이하(문자열 비교). */
export function noticeProblem(draft: NoticeDraft, todayKst: string): string | null {
  if (!dateInRange(draft.occurredOn, todayKst)) return `알린 날은 ${DATE_MIN} ~ ${todayKst}(오늘) 사이로 입력해 주세요.`;
  if (draft.summary.trim() === "") return "요지를 입력해 주세요(메일·전화 등 수단도 요지에 적습니다).";
  return null;
}

export const noticeBody = (draft: NoticeDraft): Record<string, unknown> => ({
  occurred_on: draft.occurredOn,
  ...(draft.partner === null ? {} : { counterpart_partner_id: draft.partner.id }),
  summary: draft.summary.trim(),
});

export function NoticeFields({
  draft,
  onChange,
  todayKst,
}: {
  draft: NoticeDraft;
  onChange: (next: NoticeDraft) => void;
  todayKst: string;
}) {
  const dateId = useId();
  const summaryId = useId();
  const typeId = useId();
  return (
    <div className="flex flex-col gap-3 text-sm">
      <p className="break-keep rounded bg-gray-50 p-2 text-xs text-gray-600">
        이 시스템은 메일을 보내지 않습니다. 실제로 알린 사실을 기록합니다.
      </p>
      <div className="flex flex-col gap-1">
        <label htmlFor={dateId} className="cell-nowrap text-gray-600">
          알린 날 (현지 날짜, 필수)
        </label>
        <input
          id={dateId}
          type="date"
          min={DATE_MIN}
          max={todayKst}
          value={draft.occurredOn}
          aria-required="true"
          onChange={(event) => onChange({ ...draft, occurredOn: event.target.value })}
          className={`${inputClass} w-44`}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={typeId} className="cell-nowrap text-gray-600">
          상대 유형 (선택)
        </label>
        <select
          id={typeId}
          value={draft.partnerType}
          onChange={(event) => onChange({ ...draft, partnerType: event.target.value, partner: null })}
          className={inputClass}
        >
          <option value="">상대 지정 안 함</option>
          {NOTICE_PARTNER_TYPES.map((code) => (
            <option key={code} value={code}>
              {partnerTypeLabel(code)}
            </option>
          ))}
        </select>
        {draft.partnerType !== "" && (
          <SearchSelect<Partner>
            key={draft.partnerType}
            label={`${partnerTypeLabel(draft.partnerType)} 거래처`}
            path="/v1/partners"
            params={{ type: draft.partnerType }}
            queryKey={["partners"]}
            value={draft.partner}
            onChange={(next) => onChange({ ...draft, partner: next })}
            getKey={(item) => item.id}
            getLabel={(item) => item.name_ko}
          />
        )}
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={summaryId} className="cell-nowrap text-gray-600">
          요지 (필수)
        </label>
        <textarea
          id={summaryId}
          rows={3}
          maxLength={SUMMARY_MAX}
          aria-required="true"
          value={draft.summary}
          onChange={(event) => onChange({ ...draft, summary: event.target.value })}
          className={inputClass}
        />
      </div>
    </div>
  );
}

// ── 계획·실적 대화상자 ──

interface ValueDialogProps<B extends MilestoneBoard> {
  mode: MilestoneEditMode;
  /** 연 순간의 행(기준 version 고정). */
  row: MilestoneRow;
  /** 예: `/v1/shipments/31/milestones` · `/v1/purchase-orders/7/milestones`. */
  basePath: string;
  /** 낙관 잠금 안내문의 대상 이름('선적'·'발주'). */
  noun: string;
  /** 시각형 기본 시간대(출발국 KR → Asia/Seoul, 그 밖은 null = 기본값 없음·필수 — design-D D12). */
  defaultZone: string | null;
  /** 서버 KST 오늘(보드 `today_kst`) — 통보 날짜 상한. */
  todayKst: string;
  /** 선적 롤오버의 '통보도 지금 기록' 경로(`/v1/shipments/{id}/milestone-changes`) — 없으면 선택지 없음(OEM). */
  noticeBasePath?: string;
  onSaved: (result: MilestoneWriteResult<B>) => void;
  onNoticeSaved?: () => void;
  onClose: () => void;
  /** 겹친 편집(409) — 닫고 최신 보드를 다시 받는다. */
  onReload: () => void;
}

function existingInstant(row: MilestoneRow): InstantValue | null {
  if (isInstant(row.planned)) return row.planned;
  if (isInstant(row.actual)) return row.actual;
  return null;
}

export function MilestoneValueDialog<B extends MilestoneBoard>({
  mode,
  row,
  basePath,
  noun,
  defaultZone,
  todayKst,
  noticeBasePath,
  onSaved,
  onNoticeSaved,
  onClose,
  onReload,
}: ValueDialogProps<B>) {
  const titleRef = useRef<HTMLHeadingElement | null>(null);
  const firstRef = useRef<HTMLInputElement | null>(null);
  const dateId = useId();
  const wallId = useId();
  const zoneId = useId();
  const reasonId = useId();
  const problemId = useId();
  const datetime = row.value_shape === "DATETIME";
  const current = mode === "plan" ? row.planned : row.actual;
  const other = mode === "plan" ? row.actual : row.planned;
  const hasValue = current !== null;
  const label = milestoneTypeLabel(row.milestone_type);
  const rollover = mode === "plan" && hasValue;

  // 시간대 — 다른 쪽 값이 있으면 같은 시간대만(서버 422 — 계획·실적은 같은 장소의 사건). 저장된 시간대를 서버가 해석하지 못하는 행
  // (TZ_UNRESOLVED)은 새 시간대로 바꾸는 탈출로가 열려 있다(서버 예외와 같다).
  const stored = existingInstant(row);
  const zoneLocked = datetime && other !== null && stored !== null && row.unknown_reason === null;
  const initialZone =
    stored !== null && row.unknown_reason === null ? stored.tz : row.unknown_reason !== null ? "" : (defaultZone ?? "");
  const initialWall = isInstant(current) ? (utcToZonedWallTime(current.at_utc, current.tz) ?? "") : "";
  const initialDate = typeof current === "string" ? current : "";

  const [dateValue, setDateValue] = useState(initialDate);
  const [wall, setWall] = useState(initialWall);
  const [zone, setZone] = useState(initialZone);
  const [clear, setClear] = useState(false);
  const [reason, setReason] = useState("");
  const [withNotice, setWithNotice] = useState(false);
  const [notice, setNotice] = useState<NoticeDraft>(() => emptyNotice(todayKst));
  const [phase, setPhase] = useState<"form" | "notice-failed">("form");
  const [changeId, setChangeId] = useState<number | null>(null);
  const [keys] = useState(() => createKeyKeeper());
  const [noticeKeys] = useState(() => createKeyKeeper());
  const lock = useRef(false);
  const [zones] = useState(() => timeZoneChoices(stored?.tz ?? null));

  useEffect(() => {
    if (phase === "notice-failed") titleRef.current?.focus();
  }, [phase]);

  const write = useMutation({
    mutationFn: async (input: { key: string; body: Record<string, unknown> }) => {
      try {
        return await apiFetch<MilestoneWriteResult<B>>(`${basePath}/${row.milestone_type}/${mode}`, {
          method: "POST",
          idempotencyKey: input.key,
          body: input.body,
        });
      } finally {
        lock.current = false;
      }
    },
  });

  const noticeWrite = useMutation({
    mutationFn: async (input: { changeId: number; key: string; body: Record<string, unknown> }) => {
      try {
        return await apiFetch<MilestoneChange>(`${noticeBasePath}/${input.changeId}/notices`, {
          method: "POST",
          idempotencyKey: input.key,
          body: input.body,
        });
      } finally {
        lock.current = false;
      }
    },
  });

  const pending = write.isPending || noticeWrite.isPending;

  // ── 입력 판정 ──
  const converted = datetime && !clear && wall !== "" && zone !== "" ? zonedWallTimeToUtc(wall, zone) : null;
  let valueProblem: string | null = null;
  if (!clear) {
    if (datetime) {
      if (wall === "") valueProblem = "날짜와 시각을 입력해 주세요.";
      else if (zone === "") valueProblem = "시간대를 골라 주세요(기본값 없음 — 그 장소의 시간대).";
      else if (converted !== null && !converted.ok) valueProblem = converted.problem;
      else if (wall < DATETIME_MIN || wall > DATETIME_MAX) valueProblem = `${DATE_MIN} ~ ${DATE_MAX} 범위의 시각만 받습니다.`;
    } else if (!dateInRange(dateValue)) {
      valueProblem = dateValue === "" ? "날짜를 입력해 주세요." : `${DATE_MIN} ~ ${DATE_MAX} 범위의 날짜만 받습니다.`;
    }
  }
  const unchanged =
    !clear && (datetime ? wall === initialWall && zone === initialZone && initialWall !== "" : dateValue === initialDate && initialDate !== "");
  const reasonRequired = hasValue;
  const reasonMissing = reasonRequired && reason.trim() === "";
  const noticeIssue = rollover && withNotice ? noticeProblem(notice, todayKst) : null;
  const blocked = pending || valueProblem !== null || unchanged || reasonMissing || noticeIssue !== null;

  function buildBody(): Record<string, unknown> {
    const reasonText = reasonRequired ? reason.trim() : null;
    if (clear) return actualBody(row, null, reasonText);
    const value: { on: string } | { at: string; tz: string } =
      datetime && converted !== null && converted.ok ? { at: converted.iso, tz: zone } : { on: dateValue };
    return mode === "plan" ? planBody(row, value, reasonText) : actualBody(row, value, reasonText);
  }

  function sendNotice(id: number) {
    const body = noticeBody(notice);
    noticeWrite.mutate(
      { changeId: id, key: noticeKeys.keyFor(JSON.stringify({ id, ...body })), body },
      {
        onSuccess: () => {
          onNoticeSaved?.();
          onClose();
        },
        onError: () => setPhase("notice-failed"),
      },
    );
  }

  function submit() {
    if (blocked || lock.current) return; // 동기 잠금 — isPending은 한 틱 늦게 켜진다
    lock.current = true;
    const body = buildBody();
    write.mutate(
      { key: keys.keyFor(JSON.stringify(body)), body },
      {
        onSuccess: (result) => {
          onSaved(result);
          if (rollover && withNotice && result.change !== null && noticeBasePath !== undefined) {
            setChangeId(result.change.id);
            lock.current = true;
            sendNotice(result.change.id);
          } else {
            onClose();
          }
        },
      },
    );
  }

  function retryNotice() {
    if (changeId === null || lock.current) return;
    lock.current = true;
    sendNotice(changeId);
  }

  const title =
    mode === "plan"
      ? hasValue
        ? `${ROLLOVER_TYPES.has(row.milestone_type) ? "계획 변경(롤오버)" : "계획 변경"} — ${label}`
        : `계획 입력 — ${label}`
      : hasValue
        ? `실적 정정 — ${label}`
        : `실적 입력 — ${label}`;

  if (phase === "notice-failed") {
    return (
      <DialogShell title={`${label} — 통보 기록 실패`} titleRef={titleRef} pending={pending} onClose={onClose}>
        <p role="alert" className="mt-3 break-keep text-sm text-signal-red">
          롤오버는 기록됐고 통보 기록은 실패했습니다 — {writeErrorText(noticeWrite.error, noun)}
        </p>
        <p className="mt-2 break-keep text-sm text-gray-600">
          &lsquo;다시 시도&rsquo;는 같은 통보를 같은 요청(같은 키)으로 다시 보냅니다. 닫으면 아래 &lsquo;마일스톤 변경 이력&rsquo;에서 다시 기록할 수
          있습니다.
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={pending}
            className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm disabled:opacity-50"
          >
            닫기
          </button>
          <button
            type="button"
            onClick={retryNotice}
            disabled={pending}
            className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {pending ? "처리 중…" : "다시 시도"}
          </button>
        </div>
      </DialogShell>
    );
  }

  const describedBy = [valueProblem !== null ? problemId : null].filter(Boolean).join(" ") || undefined;

  return (
    <DialogShell title={title} titleRef={titleRef} pending={pending} onClose={onClose} initialFocus={firstRef}>
      <form
        className="mt-3 flex flex-col gap-3 text-sm"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <fieldset disabled={pending} className="contents">
          {hasValue && (
            <p className="break-keep text-gray-600">
              {mode === "plan" ? "이전 계획" : "이전 실적"}: <MilestoneValueText value={current} />
            </p>
          )}
          {mode === "actual" && row.milestone_type !== "CUSTOMS_CLEARED" && (
            <p className="break-keep text-xs text-gray-500">
              실제로 일어난 날짜·시각만 기록합니다(아직 오지 않은 날짜·시각은 서버가 거절합니다).
              {["ETD", "BL_ISSUED", "ETA"].includes(row.milestone_type) && " ETD·B/L 발행·ETA 실적은 출고지시 뒤에만 기록합니다."}
            </p>
          )}

          {datetime ? (
            <>
              <div className="flex flex-col gap-1">
                <label htmlFor={wallId} className="cell-nowrap text-gray-600">
                  {mode === "plan" ? "계획 시각" : "실적 시각"} (그 장소의 현지 시각)
                </label>
                <input
                  id={wallId}
                  ref={firstRef}
                  type="datetime-local"
                  min={DATETIME_MIN}
                  max={DATETIME_MAX}
                  value={wall}
                  disabled={clear}
                  aria-invalid={valueProblem !== null && wall !== ""}
                  aria-describedby={describedBy}
                  onChange={(event) => setWall(event.target.value)}
                  className={`${inputClass} w-60`}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor={zoneId} className="cell-nowrap text-gray-600">
                  시간대 (IANA, 필수)
                </label>
                <select
                  id={zoneId}
                  value={zone}
                  disabled={clear || zoneLocked}
                  aria-required="true"
                  onChange={(event) => setZone(event.target.value)}
                  className={inputClass}
                >
                  <option value="">선택</option>
                  {zones.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
                {zoneLocked && (
                  <span className="break-keep text-xs text-gray-500">
                    {mode === "plan" ? "실적" : "계획"}과 같은 시간대여야 합니다(같은 장소의 사건).
                  </span>
                )}
                {row.unknown_reason !== null && (
                  <span className="break-keep text-xs text-signal-red">
                    저장된 시간대를 확인할 수 없습니다 — 올바른 시간대를 다시 골라 주세요.
                  </span>
                )}
              </div>
              {converted !== null && converted.ok && (
                <p role="status" className="break-keep rounded bg-gray-50 p-2 text-xs text-gray-700">
                  저장될 시각: {toZonedPairDisplay(converted.iso, zone)}
                  {converted.ambiguous && " — 서머타임 전환으로 같은 시각이 두 번 있어 이른 쪽으로 저장합니다."}
                </p>
              )}
            </>
          ) : (
            <div className="flex flex-col gap-1">
              <label htmlFor={dateId} className="cell-nowrap text-gray-600">
                {mode === "plan" ? "계획일" : "실적일"} (현지 날짜 — 서류에 찍힌 날짜)
              </label>
              <input
                id={dateId}
                ref={firstRef}
                type="date"
                min={DATE_MIN}
                max={DATE_MAX}
                value={dateValue}
                disabled={clear}
                aria-invalid={valueProblem !== null && dateValue !== ""}
                aria-describedby={describedBy}
                onChange={(event) => setDateValue(event.target.value)}
                className={`${inputClass} w-44`}
              />
            </div>
          )}

          {mode === "actual" && hasValue && (
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={clear} onChange={(event) => setClear(event.target.checked)} />
              <span className="break-keep">실적 지우기(잘못 입력한 실적 — 정정 사유 필수)</span>
            </label>
          )}

          {reasonRequired && (
            <div className="flex flex-col gap-1">
              <label htmlFor={reasonId} className="cell-nowrap text-gray-600">
                {mode === "plan" ? "변경 사유 (필수)" : "정정 사유 (필수)"}
              </label>
              <textarea
                id={reasonId}
                rows={2}
                maxLength={REASON_MAX}
                aria-required="true"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                className={inputClass}
              />
              {rollover && ROLLOVER_TYPES.has(row.milestone_type) && (
                <span className="break-keep text-xs text-gray-500">계획 변경은 롤오버 이력으로 남습니다(지울 수 없음).</span>
              )}
            </div>
          )}

          {rollover && noticeBasePath !== undefined && (
            <>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={withNotice} onChange={(event) => setWithNotice(event.target.checked)} />
                <span className="break-keep">통보도 지금 기록</span>
              </label>
              {withNotice && <NoticeFields draft={notice} onChange={setNotice} todayKst={todayKst} />}
            </>
          )}
        </fieldset>

        {valueProblem !== null && (dateValue !== "" || wall !== "" || zone !== "") && (
          <p id={problemId} role="alert" className="break-keep text-xs text-signal-red">
            {valueProblem}
          </p>
        )}
        {unchanged && <p className="text-xs text-gray-500">바뀐 값이 없습니다.</p>}
        {reasonMissing && <span className="sr-only">사유를 입력해야 저장할 수 있습니다.</span>}
        {noticeIssue !== null && (notice.summary !== "" || notice.occurredOn !== todayKst) && (
          <p role="alert" className="break-keep text-xs text-signal-red">
            {noticeIssue}
          </p>
        )}

        {write.error && (
          <div role="alert" className="break-keep text-sm text-signal-red">
            <p>{writeErrorText(write.error, noun)}</p>
            {needsBoardReload(write.error) && (
              <button
                type="button"
                onClick={onReload}
                className="cell-nowrap mt-2 rounded border border-gray-300 px-3 py-1 text-sm text-gray-900"
              >
                최신 내용 불러오기
              </button>
            )}
          </div>
        )}

        <div className="mt-2 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={pending}
            className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm disabled:opacity-50"
          >
            닫기
          </button>
          <button
            type="submit"
            disabled={blocked}
            className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {pending ? "처리 중…" : rollover && withNotice ? "변경 + 통보 기록 저장" : "저장"}
          </button>
        </div>
      </form>
    </DialogShell>
  );
}

// ── 변경 이력의 '통보 기록' 대화상자 ──

export function MilestoneNoticeDialog({
  change,
  path,
  todayKst,
  noun,
  onSaved,
  onClose,
}: {
  change: MilestoneChange;
  /** `/v1/shipments/{id}/milestone-changes/{change_id}/notices`. */
  path: string;
  todayKst: string;
  noun: string;
  onSaved: (next: MilestoneChange) => void;
  onClose: () => void;
}) {
  const titleRef = useRef<HTMLHeadingElement | null>(null);
  const [draft, setDraft] = useState<NoticeDraft>(() => emptyNotice(todayKst));
  const [keys] = useState(() => createKeyKeeper());
  const lock = useRef(false);

  const save = useMutation({
    mutationFn: async (input: { key: string; body: Record<string, unknown> }) => {
      try {
        return await apiFetch<MilestoneChange>(path, { method: "POST", idempotencyKey: input.key, body: input.body });
      } finally {
        lock.current = false;
      }
    },
    onSuccess: (next) => {
      onSaved(next);
      onClose();
    },
  });

  const problem = noticeProblem(draft, todayKst);
  const blocked = save.isPending || problem !== null;

  return (
    <DialogShell
      title={`통보 기록 — ${milestoneTypeLabel(change.milestone_type)} ${changeKindLabel(change.change_kind, change.milestone_type)}`}
      titleRef={titleRef}
      pending={save.isPending}
      onClose={onClose}
    >
      <form
        className="mt-3 flex flex-col gap-3 text-sm"
        onSubmit={(event) => {
          event.preventDefault();
          if (blocked || lock.current) return;
          lock.current = true;
          const body = noticeBody(draft);
          save.mutate({ key: keys.keyFor(JSON.stringify(body)), body });
        }}
      >
        <p className="break-keep text-gray-600">
          <MilestoneValueText value={change.old} /> → <MilestoneValueText value={change.new} />
          {change.reason !== null && <span className="ml-1">(사유: {change.reason})</span>}
        </p>
        <fieldset disabled={save.isPending} className="contents">
          <NoticeFields draft={draft} onChange={setDraft} todayKst={todayKst} />
        </fieldset>
        {problem !== null && draft.summary !== "" && (
          <p role="alert" className="break-keep text-xs text-signal-red">
            {problem}
          </p>
        )}
        {save.error && (
          <p role="alert" className="break-keep text-sm text-signal-red">
            {writeErrorText(save.error, noun)}
          </p>
        )}
        <div className="mt-2 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={save.isPending}
            className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm disabled:opacity-50"
          >
            닫기
          </button>
          <button
            type="submit"
            disabled={blocked}
            className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {save.isPending ? "처리 중…" : "통보 기록 저장"}
          </button>
        </div>
      </form>
    </DialogShell>
  );
}
