// 인증 보드 — 칸반 + 캘린더 (DESIGN.md §14 ④ / ADR-0036 / S2-3 PR-3 안건 ⑥).
//
// ★ 읽기 전용 화면이다 — **카드를 끌어 상태를 바꾸는 기능은 없다**(판정 요청 16 확정).
//   상태 전이는 사람이 사유·부속 데이터(신청일·승인일 등)를 함께 적는 기록이고, 드래그 한 번으로
//   할 수 있는 동작이 아니다. 전이 통로는 인증 화면의 상세 폼 하나다(카드를 누르면 거기로 간다).
// ★ 칸반 컬럼 = 인증 상태 11값(§5.2 — 서버 machine.py와 같은 값·같은 라벨 표).
// ★ 잘림은 숨기지 않는다 — 칸반은 200장, 캘린더는 한 달 200건까지만 싣고, 넘으면 총계와 함께
//   알린다(관찰 등재 #3 — 조용한 잘림 금지).
// ★ 캘린더는 기일 엔진의 화면이다: 인증 만료일 + 문서 유효기간, 도과는 서버가 계산한 값(is_overdue).

import { useState } from "react";
import { Link } from "react-router";
import { ListState } from "../components/list-state";
import { todayKst } from "../lib/datetime";
import { certificationStatusLabel } from "../lib/labels";
import { usePagedQuery } from "../lib/paging";
import type { Certification } from "./certifications";

/** 칸반 컬럼 순서 — §5.2 진행 순(미착수→…→승인→만료임박→갱신중)에 종결 계열을 뒤에 둔다. */
export const BOARD_COLUMNS = [
  "NOT_STARTED",
  "PREPARING",
  "SUBMITTED",
  "IN_REVIEW",
  "SUPPLEMENTING",
  "APPROVED",
  "EXPIRING",
  "RENEWING",
  "EXPIRED",
  "REJECTED",
  "SUSPENDED",
] as const;

/** 칸반이 한 번에 싣는 카드 수 — 서버 페이지 상한(§18.4)과 같다. */
export const BOARD_CARD_LIMIT = 200;

export interface CalendarItem {
  kind: "CERTIFICATION" | "DOCUMENT";
  id: number;
  title: string;
  date: string;
  status: string | null;
  is_overdue: boolean;
  assignee_id: number | null;
  assignee_name: string | null;
}

function TruncationNotice({ total, shown, what }: { total: number; shown: number; what: string }) {
  if (total <= shown) return null;
  return (
    <p role="status" className="mt-3 rounded border border-signal-amber/60 p-2 text-sm">
      전체 {total}건 중 {shown}건만 표시했습니다. {what}
    </p>
  );
}

// ── 칸반 ─────────────────────────────────────────────────────────────────────

function KanbanCard({ row }: { row: Certification }) {
  return (
    <li>
      <Link
        to={`/certifications?id=${row.id}`}
        className="block rounded border border-gray-200 bg-white p-2 text-sm hover:border-gray-400 dark:bg-black"
      >
        <span className="cell-nowrap text-xs text-gray-500">{row.market_code}</span>
        <span className="block font-medium">{row.template_name}</span>
        <span className="block text-gray-600">{row.target_label}</span>
        <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
          <span className="cell-nowrap">
            {row.expires_on === null ? "만료일 없음" : `만료 ${row.expires_on}`}
          </span>
          {row.is_overdue && (
            <span className="cell-nowrap rounded bg-red-100 px-1.5 py-0.5 font-semibold text-red-800">
              도과 {row.overdue_days ?? 0}일
            </span>
          )}
          <span className="cell-nowrap">
            {row.assignee_name === null ? "담당자 없음" : `담당 ${row.assignee_name}`}
          </span>
        </span>
      </Link>
    </li>
  );
}

function Kanban() {
  const list = usePagedQuery<Certification>(
    ["certification-board", "kanban"],
    `/v1/certifications?size=${BOARD_CARD_LIMIT}`,
  );
  const items = list.data?.items ?? [];
  const byStatus = new Map<string, Certification[]>();
  for (const row of items) {
    byStatus.set(row.status, [...(byStatus.get(row.status) ?? []), row]);
  }

  return (
    <div>
      <p className="mt-3 text-sm text-gray-500">
        상태는 카드를 눌러 <strong>인증 상세의 전이 폼</strong>에서만 바꿀 수 있습니다 — 카드를
        끌어서 옮기는 기능은 없습니다(전이에는 사유·신청일 같은 기록이 함께 필요합니다).
      </p>
      <ListState
        isPending={list.isPending}
        error={list.error}
        isEmpty={items.length === 0}
        emptyHint="등록된 인증이 없습니다. 인증 화면에서 확정 템플릿으로 인증을 등록하세요."
      >
        <TruncationNotice
          total={list.data?.total ?? 0}
          shown={items.length}
          what="나머지는 인증 화면에서 상태·요건으로 좁혀 확인해 주세요."
        />
        <div className="mt-3 overflow-x-auto pb-2">
          <div className="flex gap-3">
            {BOARD_COLUMNS.map((status) => {
              const cards = byStatus.get(status) ?? [];
              return (
                <section
                  key={status}
                  aria-label={`${certificationStatusLabel(status)} 컬럼`}
                  className="w-56 shrink-0 rounded-lg bg-gray-50 p-2 dark:bg-gray-900"
                >
                  <h2 className="cell-nowrap mb-2 text-sm font-semibold">
                    {certificationStatusLabel(status)}{" "}
                    <span className="num font-normal text-gray-500">{cards.length}</span>
                  </h2>
                  <ul className="space-y-2">
                    {cards.map((row) => (
                      <KanbanCard key={row.id} row={row} />
                    ))}
                  </ul>
                </section>
              );
            })}
          </div>
        </div>
      </ListState>
    </div>
  );
}

// ── 캘린더 ───────────────────────────────────────────────────────────────────

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"] as const;

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** 'YYYY-MM-DD'의 월 시작·끝 — 문자열·UTC 산술만 쓴다(브라우저 시간대 영향 없음). */
function monthBounds(
  year: number,
  month: number,
): { from: string; to: string; days: number; startDay: number } {
  const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return {
    from: `${year}-${pad(month)}-01`,
    to: `${year}-${pad(month)}-${pad(days)}`,
    days,
    startDay: new Date(Date.UTC(year, month - 1, 1)).getUTCDay(),
  };
}

function shiftMonth(year: number, month: number, delta: number): { year: number; month: number } {
  const index = year * 12 + (month - 1) + delta;
  return { year: Math.floor(index / 12), month: (index % 12) + 1 };
}

function CalendarChip({ item }: { item: CalendarItem }) {
  const isCertification = item.kind === "CERTIFICATION";
  const tone = item.is_overdue
    ? "bg-red-100 text-red-900"
    : "bg-gray-100 text-gray-900 dark:bg-gray-800 dark:text-gray-100";
  const label = `${isCertification ? "인증" : "문서"} ${item.title}${item.is_overdue ? " (도과)" : ""}`;
  return (
    <Link
      to={isCertification ? `/certifications?id=${item.id}` : "/documents"}
      title={label}
      aria-label={label}
      className={`block truncate rounded px-1 py-0.5 text-xs ${tone}`}
    >
      <span className="font-semibold">{isCertification ? "인증" : "문서"}</span> {item.title}
    </Link>
  );
}

function Calendar() {
  const today = todayKst();
  const [cursor, setCursor] = useState(() => ({
    year: Number(today.slice(0, 4)),
    month: Number(today.slice(5, 7)),
  }));
  const bounds = monthBounds(cursor.year, cursor.month);
  const path = `/v1/deadlines/calendar?from=${bounds.from}&to=${bounds.to}&size=${BOARD_CARD_LIMIT}`;
  const list = usePagedQuery<CalendarItem>(["certification-board", "calendar", bounds.from], path);
  const items = list.data?.items ?? [];

  const byDay = new Map<number, CalendarItem[]>();
  for (const item of items) {
    const day = Number(item.date.slice(8, 10));
    byDay.set(day, [...(byDay.get(day) ?? []), item]);
  }
  const cells: (number | null)[] = [
    ...Array<null>(bounds.startDay).fill(null),
    ...Array.from({ length: bounds.days }, (_, index) => index + 1),
  ];
  while (cells.length % 7 !== 0) cells.push(null);

  return (
    <div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => setCursor((prev) => shiftMonth(prev.year, prev.month, -1))}
          className="rounded border border-gray-300 px-2 py-1 text-sm"
        >
          이전 달
        </button>
        <h2 className="cell-nowrap text-lg font-semibold">
          {cursor.year}년 {cursor.month}월
        </h2>
        <button
          type="button"
          onClick={() => setCursor((prev) => shiftMonth(prev.year, prev.month, 1))}
          className="rounded border border-gray-300 px-2 py-1 text-sm"
        >
          다음 달
        </button>
        <button
          type="button"
          onClick={() =>
            setCursor({ year: Number(today.slice(0, 4)), month: Number(today.slice(5, 7)) })
          }
          className="rounded border border-gray-300 px-2 py-1 text-sm"
        >
          이번 달
        </button>
        <span className="text-sm text-gray-500">
          인증 만료일과 문서 유효기간입니다. 붉은 항목은 기일이 지난 건입니다(계산값).
        </span>
      </div>

      {list.error ? (
        <ListState isPending={false} error={list.error} isEmpty={false} emptyHint="">
          {null}
        </ListState>
      ) : (
        <>
          <TruncationNotice
            total={list.data?.total ?? 0}
            shown={items.length}
            what="이 달의 나머지 기일은 인증·문서 화면에서 확인해 주세요."
          />
          <div className="mt-3 grid grid-cols-7 gap-px overflow-hidden rounded-lg border border-gray-200 bg-gray-200">
            {WEEKDAYS.map((weekday) => (
              <div
                key={weekday}
                className="cell-nowrap bg-gray-50 px-2 py-1 text-center text-xs font-semibold dark:bg-gray-900"
              >
                {weekday}
              </div>
            ))}
            {cells.map((day, index) => {
              if (day === null)
                return <div key={`blank-${index}`} className="min-h-24 bg-white dark:bg-black" />;
              const isToday = `${bounds.from.slice(0, 8)}${pad(day)}` === today;
              const dayItems = byDay.get(day) ?? [];
              return (
                <div
                  key={day}
                  aria-label={`${cursor.month}월 ${day}일${dayItems.length > 0 ? ` ${dayItems.length}건` : ""}`}
                  className={`min-h-24 bg-white p-1 dark:bg-black ${isToday ? "ring-2 ring-inset ring-gray-900 dark:ring-gray-100" : ""}`}
                >
                  <span className={`num block text-xs ${isToday ? "font-bold" : "text-gray-500"}`}>
                    {day}
                    {isToday && " (오늘)"}
                  </span>
                  <div className="mt-1 space-y-1">
                    {dayItems.map((item) => (
                      <CalendarChip key={`${item.kind}-${item.id}`} item={item} />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
          {list.data !== undefined && items.length === 0 && (
            <p className="mt-3 text-sm text-gray-500">
              이 달에는 만료일·유효기간이 있는 항목이 없습니다.
            </p>
          )}
        </>
      )}
    </div>
  );
}

// ── 페이지 ───────────────────────────────────────────────────────────────────

type Tab = "kanban" | "calendar";

export function CertificationBoardPage() {
  const [tab, setTab] = useState<Tab>("kanban");
  const tabs: { id: Tab; label: string }[] = [
    { id: "kanban", label: "칸반" },
    { id: "calendar", label: "캘린더" },
  ];
  return (
    <section>
      <header>
        <h1 className="text-2xl font-bold">인증 보드</h1>
        <p className="mt-1 text-sm text-gray-500">
          인증 진행 현황을 상태별 칸반과 기일 캘린더로 봅니다. 카드·항목을 누르면 상세로 이동합니다.
        </p>
      </header>
      <div role="tablist" aria-label="보드 보기" className="mt-4 flex gap-2">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            onClick={() => setTab(item.id)}
            className={`rounded border px-3 py-1 text-sm ${tab === item.id ? "border-gray-900 font-semibold dark:border-gray-100" : "border-gray-300 text-gray-600"}`}
          >
            {item.label}
          </button>
        ))}
      </div>
      {tab === "kanban" ? <Kanban /> : <Calendar />}
    </section>
  );
}
