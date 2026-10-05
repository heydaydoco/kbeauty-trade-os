// 마일스톤 타임라인 — 선적 상세·OEM 생산 일정이 같이 쓰는 세로 카드 목록 (S3-2 PR-4b — design-D D6·D7·D13 / PROGRESS 'S3-2 PR-4a'·'PR-4c' 인계 계약).
//
// ★ 판정은 전부 서버 값이다(D-N `days_left`·도과 `is_overdue`·유효값 `effective`·파생 `derived`·휴일 `holiday`·부분 수리 `customs_state`) —
//   화면 날짜 산술 0. 날짜형 값은 'YYYY-MM-DD' 문자열 그대로 + '(현지)', 시각형은 KST·현지 병기(toZonedPairDisplay).
// ★ 버튼은 부모가 준 `canEdit`(= 서버 allowed_actions의 EDIT_MILESTONES)일 때만, 그것도 **저장형 행만** — 파생 행(자동 계산)은 버튼 0,
//   신고수리 실적은 버튼 대신 '통관 기록에서 입력합니다' 안내(입력처 = 통관 기록 — X-02).
// ★ 휴일 배지는 ETA(도착국)만 서버가 준다(R-09): HOLIDAY = 경고 + 이름 / UNVERIFIED = '휴일 캘린더 미등록 — 확인 불가' + 캘린더 링크 /
//   CLEAR = 배지 없음. UNKNOWN은 빈칸·0·'-'로 그리지 않는다(사유 한글).
// ★ 390px: 표가 아니라 카드라 가로 스크롤이 없다. 종류명·날짜·D-N·배지는 nowrap, 설명·사유는 break-keep.

import { Link } from "react-router";
import { toZonedPairDisplay } from "../lib/datetime";
import {
  customsBadge,
  derivedText,
  dueText,
  fulfilmentLabel,
  holidayBadge,
  isInstant,
  milestoneTypeLabel,
  type Badge,
  type MilestoneBoard,
  type MilestoneRow,
  type MilestoneValue,
} from "../lib/milestone";

export type MilestoneEditMode = "plan" | "actual";

const BADGE_TONE: Record<Badge["tone"], string> = {
  warn: "border-signal-amber bg-signal-amber/20 text-gray-900",
  muted: "border-gray-400 bg-gray-100 text-gray-700",
  alert: "border-signal-red text-signal-red",
};

export function BadgeChip({ badge }: { badge: Badge }) {
  const chip = <span className={`cell-nowrap rounded border px-1.5 py-0.5 text-xs ${BADGE_TONE[badge.tone]}`}>{badge.text}</span>;
  if (badge.link === undefined) return chip;
  return (
    <Link to={badge.link} className="underline decoration-dotted">
      {chip}
    </Link>
  );
}

/** 값 1칸 — 날짜형 'YYYY-MM-DD (현지)', 시각형 'KST · 현지' 병기, 없으면 '미입력'. */
export function MilestoneValueText({ value }: { value: MilestoneValue }) {
  if (value === null) return <span className="text-gray-400">미입력</span>;
  if (isInstant(value)) return <span className="num">{toZonedPairDisplay(value.at_utc, value.tz)}</span>;
  return (
    <span className="num cell-nowrap">
      {value} <span className="text-xs text-gray-500">(현지)</span>
    </span>
  );
}

/** 휴일 요약 배너 — 경고만, 차단 없음(design-D D6 ③). 0건이면 그리지 않는다. */
export function HolidaySummaryBanner({ summary }: { summary: MilestoneBoard["holiday_summary"] }) {
  if (summary.holiday === 0 && summary.unverified === 0) return null;
  const parts = [
    summary.holiday > 0 ? `휴일 경고 ${summary.holiday}건` : null,
    summary.unverified > 0 ? `휴일 캘린더 미등록 ${summary.unverified}건 — 확인 불가` : null,
  ].filter((part): part is string => part !== null);
  return (
    <p role="note" className="break-keep rounded border border-signal-amber bg-signal-amber/20 p-3 text-sm">
      {parts.join(" · ")}. 날짜는 옮기지 않습니다(경고만) — 아래 ETA 카드에서 확인해 주세요.
    </p>
  );
}

interface TimelineProps {
  board: MilestoneBoard;
  /** 서버 allowed_actions의 EDIT_MILESTONES — 화면이 역할·상태로 다시 판정하지 않는다. */
  canEdit: boolean;
  onEdit: (mode: MilestoneEditMode, row: MilestoneRow) => void;
  /** 신고수리 실적 안내가 가리킬 통관 기록 섹션 id(선적 상세만). */
  customsSectionId?: string;
  /** 접근성 이름(`<ol aria-label>`) — 선적 '마일스톤', OEM '생산 일정'. */
  label: string;
}

export function MilestoneTimeline({ board, canEdit, onEdit, customsSectionId, label }: TimelineProps) {
  const shown = board.rows.filter((row) => row.applicable);
  const hidden = board.rows.length - shown.length;
  return (
    <>
      <ol aria-label={label} className="flex flex-col gap-2">
        {shown.map((row) => (
          <MilestoneCard
            key={row.milestone_type}
            row={row}
            canEdit={canEdit}
            onEdit={onEdit}
            customsSectionId={customsSectionId}
          />
        ))}
      </ol>
      {hidden > 0 && (
        <p className="mt-2 break-keep text-xs text-gray-500">
          이 선적의 구분·결제조건에 해당하지 않는 종류 {hidden}개는 숨겼습니다.
        </p>
      )}
    </>
  );
}

function MilestoneCard({
  row,
  canEdit,
  onEdit,
  customsSectionId,
}: {
  row: MilestoneRow;
  canEdit: boolean;
  onEdit: (mode: MilestoneEditMode, row: MilestoneRow) => void;
  customsSectionId?: string;
}) {
  const title = milestoneTypeLabel(row.milestone_type);
  const derived = row.kind === "DERIVED";
  const fromCustoms = row.input_source === "CUSTOMS_RECORD";
  const due = dueText(row);
  const holiday = holidayBadge(row);
  const partial = customsBadge(row);
  const datetime = row.value_shape === "DATETIME";

  return (
    <li aria-label={title} className="rounded-lg border border-gray-200 p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="cell-nowrap font-semibold">{title}</h3>
        <span className="break-keep text-xs text-gray-500">{derived ? "자동 계산 — 직접 수정 불가" : "직접 입력"}</span>
      </div>

      {derived ? (
        <p className="mt-2 text-sm">
          {row.derived === null ? (
            <span className="text-gray-500">산정 불가(기타)</span>
          ) : (
            <span className={`break-keep ${row.derived.status === "OK" ? "num" : "text-gray-700"}`}>{derivedText(row.derived)}</span>
          )}
          {row.derived?.status === "OK" && row.derived.basis !== null && (
            <span className="ml-2 cell-nowrap rounded border border-gray-300 px-1.5 py-0.5 text-xs text-gray-600">
              {row.derived.basis === "ACTUAL" ? "실적 기준" : "예정 기준(실적 입력 시 다시 계산)"}
            </span>
          )}
        </p>
      ) : (
        <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
          <dt className="cell-nowrap text-gray-500">계획</dt>
          <dd>
            <MilestoneValueText value={row.planned} />
          </dd>
          <dt className="cell-nowrap text-gray-500">실적</dt>
          <dd>
            <MilestoneValueText value={row.actual} />
            {fromCustoms && row.actual !== null && <span className="ml-1 text-xs text-gray-500">(통관 기록 수리일)</span>}
          </dd>
        </dl>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        {!derived && row.effective !== null && (
          <span className="cell-nowrap rounded border border-gray-300 px-1.5 py-0.5 text-gray-600">
            {row.effective.basis === "ACTUAL" ? "실적" : "예정 기준"}
          </span>
        )}
        {due !== null && <span className="num cell-nowrap rounded bg-gray-100 px-1.5 py-0.5 font-medium">{due}</span>}
        {row.is_overdue === true && <BadgeChip badge={{ tone: "alert", text: "도과" }} />}
        {row.fulfilment !== null && (
          <span className="cell-nowrap rounded border border-gray-300 px-1.5 py-0.5">적재 이행: {fulfilmentLabel(row.fulfilment)}</span>
        )}
        {row.unknown_reason !== null && <BadgeChip badge={{ tone: "alert", text: "시간대 확인 불가 — 판정 불가" }} />}
        {holiday !== null && <BadgeChip badge={holiday} />}
        {partial !== null && <BadgeChip badge={partial} />}
        {row.rollover_count > 0 && <span className="cell-nowrap rounded border border-gray-300 px-1.5 py-0.5">롤오버 {row.rollover_count}회</span>}
        {row.unnotified_rollovers > 0 && <BadgeChip badge={{ tone: "warn", text: `통보 기록 없음 ${row.unnotified_rollovers}` }} />}
      </div>

      {datetime && (row.scan_date !== null || row.local_date !== null) && (
        <p className="mt-1 break-keep text-xs text-gray-600">
          {row.scan_date !== null && (
            <span className="cell-nowrap">D-N 기준일 {row.scan_date}(현지·한국 날짜 중 이른 날)</span>
          )}
          {row.scan_date !== null && row.local_date !== null && " · "}
          {row.local_date !== null && <span className="cell-nowrap">현지 날짜 {row.local_date}</span>}
        </p>
      )}
      {row.order_warning === "ETA_BEFORE_ETD" && (
        <p role="note" className="mt-1 break-keep text-xs text-gray-900">
          ETA가 ETD보다 이릅니다 — 날짜변경선이 아니라면 확인해 주세요(경고만, 저장은 됩니다).
        </p>
      )}
      {row.order_warning !== null && row.order_warning !== "ETA_BEFORE_ETD" && (
        <p role="note" className="mt-1 break-keep text-xs text-gray-900">
          순서 경고(기타) — 계획·실적 날짜를 확인해 주세요.
        </p>
      )}
      {row.milestone_type === "PAYMENT_DUE" && (
        <p className="mt-1 break-keep text-xs text-gray-500">휴일 미반영(자동 이월 없음).</p>
      )}
      {fromCustoms && (
        <p className="mt-1 break-keep text-xs text-gray-600">
          실적은 통관 기록의 수리일에서 옵니다(가장 이른 수리일) —{" "}
          {customsSectionId === undefined ? (
            "통관 기록에서 입력합니다."
          ) : (
            <a href={`#${customsSectionId}`} className="underline">
              통관 기록에서 입력합니다 →
            </a>
          )}
        </p>
      )}

      {canEdit && !derived && (
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => onEdit("plan", row)}
            className="cell-nowrap rounded border border-gray-300 px-3 py-1 text-sm"
          >
            {row.planned === null ? "계획 입력" : "계획 변경"}
          </button>
          {!fromCustoms && (
            <button
              type="button"
              onClick={() => onEdit("actual", row)}
              className="cell-nowrap rounded border border-gray-300 px-3 py-1 text-sm"
            >
              {row.actual === null ? "실적 입력" : "실적 정정"}
            </button>
          )}
        </div>
      )}
    </li>
  );
}
