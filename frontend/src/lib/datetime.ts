// 시각 표시의 유일한 통로 (DESIGN.md §2 ADR-02: UTC 저장·KST 표시).
//
// 서버는 항상 UTC(ISO8601 'Z' 또는 +00:00)를 준다. 화면에 뿌릴 때만 KST로
// 바꾼다. 이 함수를 거치지 않고 날짜를 직접 포맷하면 표시 시간대가 제각각이 된다.

const KST_FORMATTER = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** UTC ISO 문자열을 'YYYY. MM. DD. HH:mm' 형태의 KST 표기로 바꾼다. */
export function toKstDisplay(isoUtc: string): string {
  const parsed = new Date(isoUtc);
  if (Number.isNaN(parsed.getTime())) {
    return "-";
  }
  return `${KST_FORMATTER.format(parsed)} (KST)`;
}

const KST_DATE_FORMATTER = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** KST 기준 오늘 날짜 'YYYY-MM-DD' — 업무 날짜는 KST다(§22 렌즈 6). 브라우저 시간대에 의존하지 않는다. */
export function todayKst(now: Date = new Date()): string {
  return KST_DATE_FORMATTER.format(now);
}

const ZONED_FORMATTERS = new Map<string, Intl.DateTimeFormat>();

function zonedFormatter(timeZone: string): Intl.DateTimeFormat {
  let formatter = ZONED_FORMATTERS.get(timeZone);
  if (formatter === undefined) {
    // 잘못된 시간대 이름이면 여기서 RangeError — 호출부가 받아 표시를 줄인다.
    formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });
    ZONED_FORMATTERS.set(timeZone, formatter);
  }
  return formatter;
}

/** 'YYYY-MM-DD HH:mm' — 부품을 직접 조립한다(로캘별 구분자 차이를 피한다). */
function formatZoned(instant: Date, timeZone: string): string {
  const parts = Object.fromEntries(zonedFormatter(timeZone).formatToParts(instant).map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

/**
 * 국제 일정 시각의 KST·현지 병기 (design-D §D12 — DESIGN §2 "UTC 저장·KST 표시(국제 일정은 현지 시간대 병기)").
 * 예: "2026-11-05 14:00 (KST) · 2026-11-05 15:00 (Asia/Tokyo)". 시간대가 Asia/Seoul이면 KST 하나만.
 * 입력은 UTC ISO **시각**뿐이다 — 날짜 문자열('YYYY-MM-DD')은 넣지 않는다(UTC 자정 해석으로 하루 밀림).
 * 감사성 시각(기록·수정 시각)은 이 함수가 아니라 `toKstDisplay`를 쓴다.
 */
export function toZonedPairDisplay(isoUtc: string, timeZone: string): string {
  // 날짜만 있는 문자열은 시각이 아니다 — 하루 밀린 값을 그리느니 '-'.
  if (/^\d{4}-\d{2}-\d{2}$/.test(isoUtc)) return "-";
  const instant = new Date(isoUtc);
  if (Number.isNaN(instant.getTime())) return "-";
  const kst = `${formatZoned(instant, "Asia/Seoul")} (KST)`;
  if (timeZone === "Asia/Seoul") return kst;
  try {
    return `${kst} · ${formatZoned(instant, timeZone)} (${timeZone})`;
  } catch {
    return `${kst} · 현지 시각 확인 불가 (${timeZone})`;
  }
}
