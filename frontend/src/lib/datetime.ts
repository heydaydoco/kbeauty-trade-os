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
