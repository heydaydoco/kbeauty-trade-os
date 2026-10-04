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

// ── 시각형 마일스톤 입력(서류마감·Cargo Closing — S3-2 PR-4b, design-D D12) ──
// 사람은 '그 시간대의 벽시계 시각'을 넣고, 서버는 UTC 오프셋이 붙은 시각 + IANA 시간대를 받는다(UTC 저장 — 렌즈 6).
// 변환은 이 파일에서만 한다(화면·마일스톤 컴포넌트는 시각 객체를 만들지 않는다 — 소스 계약).

const WALL_TIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/;
const HOUR_MS = 3_600_000;

/** 그 시각(ms)의 tz 벽시계를 UTC 기준 ms로 — 오프셋 계산용(분 단위 — 2000~2999년 오프셋은 분 단위다). */
function wallMs(instantMs: number, timeZone: string): number {
  const parts = Object.fromEntries(
    zonedFormatter(timeZone)
      .formatToParts(new Date(instantMs))
      .map((part) => [part.type, part.value]),
  );
  return Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day), Number(parts.hour), Number(parts.minute));
}

export type WallTimeResult = { ok: true; iso: string; ambiguous: boolean } | { ok: false; problem: string };

/**
 * 벽시계 시각('YYYY-MM-DDTHH:mm' — datetime-local 값) + IANA 시간대 → UTC ISO 시각.
 * 서머타임으로 **없는 시각**(앞당김 구간)은 거부(문구), **두 번 있는 시각**(되돌림 구간)은 더 이른 시각을 고르고 `ambiguous`로 알린다
 * (이른 쪽 = 기한을 더 일찍 잡는 쪽 — design-B B3 ④ '더 일찍 경고'와 같은 방향). 저장 전 미리보기로 사람이 확인한다.
 */
export function zonedWallTimeToUtc(wall: string, timeZone: string): WallTimeResult {
  const match = WALL_TIME.exec(wall);
  if (match === null) return { ok: false, problem: "날짜와 시각을 모두 입력해 주세요." };
  const target = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4]), Number(match[5]));
  if (Number.isNaN(target)) return { ok: false, problem: "날짜와 시각을 모두 입력해 주세요." };
  try {
    zonedFormatter(timeZone);
  } catch {
    return { ok: false, problem: "시간대를 확인할 수 없습니다. 목록에서 다시 골라 주세요." };
  }
  const candidates = new Set<number>();
  for (const probe of [target - 24 * HOUR_MS, target, target + 24 * HOUR_MS]) {
    const offset = wallMs(probe, timeZone) - probe;
    const candidate = target - offset;
    if (wallMs(candidate, timeZone) === target) candidates.add(candidate);
  }
  if (candidates.size === 0) {
    return { ok: false, problem: "그 시각은 이 시간대에 없습니다(서머타임 전환 구간). 다른 시각을 입력해 주세요." };
  }
  const chosen = Math.min(...candidates);
  return { ok: true, iso: new Date(chosen).toISOString(), ambiguous: candidates.size > 1 };
}

/** UTC ISO 시각 → 그 시간대의 벽시계 'YYYY-MM-DDTHH:mm'(datetime-local 초기값). 날짜 문자열·해석 불가면 null. */
export function utcToZonedWallTime(isoUtc: string, timeZone: string): string | null {
  if (/^\d{4}-\d{2}-\d{2}$/.test(isoUtc)) return null;
  const instant = new Date(isoUtc);
  if (Number.isNaN(instant.getTime())) return null;
  try {
    return formatZoned(instant, timeZone).replace(" ", "T");
  } catch {
    return null;
  }
}

/** 시간대 선택지 — 런타임이 아는 IANA 이름(국가→대표 시간대 표는 두지 않는다 — design-D D12). 저장된 값(`extra`)이 목록에 없으면 앞에 더한다. */
export function timeZoneChoices(extra: string | null = null): string[] {
  let names: string[];
  try {
    names = [...Intl.supportedValuesOf("timeZone")];
  } catch {
    names = [];
  }
  if (!names.includes("Asia/Seoul")) names = ["Asia/Seoul", ...names];
  if (extra !== null && extra !== "" && !names.includes(extra)) names = [extra, ...names];
  return names;
}
