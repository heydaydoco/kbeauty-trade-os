// 수주 전용 서버 오류 → 화면 안내 (S3-1 PR-7b). 서버가 준 한국어 message는 그대로 쓰고, 점유 문서 정보만 덧붙인다.

import { ApiError } from "./api";
import { salesOrderStatusLabel } from "./doc-status";
import { DUPLICATE_BUYER_PO_CODE, REFERENCE_ALREADY_CONVERTED_CODE } from "./sales-order";

export interface OccupantNotice {
  /** "이미 등록된 수주: SO-2026-0001 (접수)" 형태의 덧붙임 문구. */
  text: string;
  docNumber: string;
}

/**
 * 중복 바이어 PO(409 DUPLICATE_BUYER_PO)·PI당 활성 수주 1건(409 ALREADY_CONVERTED)은 detail에 점유 문서번호·상태가 온다.
 * 그 밖의 오류는 null — 호출부가 errorMessage로 처리한다. (금액은 detail에 없다.)
 */
export function occupantNotice(error: unknown): OccupantNotice | null {
  if (!(error instanceof ApiError)) return null;
  if (error.code !== DUPLICATE_BUYER_PO_CODE && error.code !== REFERENCE_ALREADY_CONVERTED_CODE) return null;
  const docNumber = error.detail.doc_number;
  const status = error.detail.status;
  if (typeof docNumber !== "string") return null;
  const label = typeof status === "string" ? ` (${salesOrderStatusLabel(status)})` : "";
  return { text: `이미 등록된 수주: ${docNumber}${label}`, docNumber };
}

export const isOccupantError = (error: unknown): boolean => occupantNotice(error) !== null;
