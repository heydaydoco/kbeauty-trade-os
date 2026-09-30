// 서버 에러 → 화면 문구 (§18.4 — 서버가 준 한국어 message를 그대로 쓴다).

import { ApiError } from "./api";

export const VERSION_CONFLICT_CODE = "COMMON.CONCURRENCY.VERSION_CONFLICT";
export const DOC_FROZEN_CODE = "TRADE_DOCS.DOCUMENT.FROZEN";

export const isVersionConflict = (error: unknown): boolean =>
  error instanceof ApiError && error.code === VERSION_CONFLICT_CODE;

/** 낙관 잠금 충돌 안내 — 무엇이 일어났고 무엇을 하면 되는지(막다른 길 금지). */
export const versionConflictGuide = (noun: string): string =>
  `다른 곳에서 이 ${noun} 정보가 먼저 수정되었습니다. '최신 내용 불러오기'로 화면을 새로 고친 뒤 다시 시도해 주세요. 입력하던 내용은 새로 고치면 사라집니다.`;

export const VERSION_CONFLICT_GUIDE =
  "다른 곳에서 이 견적이 먼저 수정되었습니다. '최신 내용 불러오기'로 화면을 새로 고친 뒤 다시 시도해 주세요. 입력하던 내용은 새로 고치면 사라집니다.";

/** `noun`은 낙관 잠금 안내문에 들어가는 전표·마스터 이름(기본 "견적"). */
export function errorMessage(
  error: unknown,
  fallback = "요청을 처리하지 못했습니다.",
  noun = "견적",
): string {
  if (isVersionConflict(error)) {
    return noun === "견적" ? VERSION_CONFLICT_GUIDE : versionConflictGuide(noun);
  }
  if (error instanceof ApiError) {
    const fields = Object.values(error.detail ?? {}).filter(
      (value): value is string => typeof value === "string",
    );
    return fields.length > 0 ? `${error.message} (${fields.join(" / ")})` : error.message;
  }
  return fallback;
}
