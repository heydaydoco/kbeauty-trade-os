// 미리보기 합계 ↔ 확정 합계 불일치 안내용 메모리 저장소 (S3-1 PR-8b 적대 검토 2).
//
// ★ 원가 문자열이므로 URL·history state·스토리지에 싣지 않고 이 모듈의 메모리에만 두며, 상세가 마운트된 뒤 지운다.
//   비교는 서버 문자열끼리의 동등 비교뿐이다(산술 없음). 원가 키가 없는 응답(total_text 없음)에서는 기록하지 않는다.

const pending = new Map<number, { preview: string; confirmed: string }>();

export function recordTotalMismatch(id: number, preview: string | undefined, confirmed: string | undefined): void {
  if (preview === undefined || confirmed === undefined || preview === confirmed) return;
  pending.set(id, { preview, confirmed });
}

/** 읽기만 한다(렌더 중 호출 — 개발 모드의 초기화 함수 이중 호출에도 안전하도록 지우지 않는다). */
export function peekTotalMismatch(id: number): { preview: string; confirmed: string } | null {
  return pending.get(id) ?? null;
}

/** 상세가 마운트된 뒤(이펙트)에 지운다 — 한 번 보여 주면 끝. */
export function clearTotalMismatch(id: number): void {
  pending.delete(id);
}
