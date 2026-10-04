// 사용자·역할 관리 (S3-2 PR-7 · design-D D14 · ADR-0086 ②) — backend identity/{router,schemas,service}.py 그대로.
//
// ★ 백엔드 변경 0: 기존 API만 쓴다 — 목록 `GET /users`(ADMIN·Page, 검색 파라미터 없음), 역할 부여 `POST /users/{id}/roles`,
//   회수 `DELETE /users/{id}/roles/{role}`(200 + UserSummary — 204 아님), 활성·비활성 `PATCH /users/{id}/active`,
//   잠금 해제 `POST /users/{id}/unlock`. 감사(identity.role.* 등)는 서버가 남긴다.
// ★ 마지막 관리자 보호(`IDENTITY.ADMIN.LAST_ONE`, 409)는 서버 판정 — 프런트가 "관리자 수"를 세어 미리 막지 않는다
//   (목록은 한 쪽뿐이고 비활성 관리자는 세지 않는 등 규칙이 서버에 있다). 서버 메시지를 그대로 보인다.
// ★ 멱등 키: 대화상자 1회 열림 = 키 1개(같은 대화상자 안 재시도는 같은 키, 새로 열면 새 키 — PR-2b 인계 계약과 같은 관례).

import { ApiError, apiFetch } from "./api";

export interface UserSummary {
  id: number;
  email: string;
  display_name: string;
  is_active: boolean;
  roles: string[];
}

export const USERS_QUERY_KEY = ["users"] as const;
export const USERS_PATH = "/v1/users";

export const LAST_ADMIN_CODE = "IDENTITY.ADMIN.LAST_ONE";

/** 역할 5종 — backend `RoleCode`·`ROLE_SEED` 순서·표시명 그대로. 이 표가 화면의 유일한 역할 라벨 출처다. */
const ROLE_LABEL: Record<string, string> = {
  ADMIN: "관리자",
  TRADE: "무역",
  LOGISTICS: "물류",
  CERT: "인증",
  VIEWER: "조회",
};

export const ROLE_CODES = Object.keys(ROLE_LABEL);

/** 모르는 코드는 "기타"(design-D 라벨 규칙) — 원문 코드는 title로 남겨 추적 가능하게. */
export const roleLabel = (code: string): string => ROLE_LABEL[code] ?? "기타";

export type UserAction =
  | { kind: "grant"; user: UserSummary; role: string }
  | { kind: "revoke"; user: UserSummary; role: string }
  | { kind: "active"; user: UserSummary; isActive: boolean }
  | { kind: "unlock"; user: UserSummary };

export function actionRequest(action: UserAction): { path: string; method: string; body?: unknown } {
  const base = `${USERS_PATH}/${action.user.id}`;
  switch (action.kind) {
    case "grant":
      return { path: `${base}/roles`, method: "POST", body: { role: action.role } };
    case "revoke":
      return { path: `${base}/roles/${encodeURIComponent(action.role)}`, method: "DELETE" };
    case "active":
      return { path: `${base}/active`, method: "PATCH", body: { is_active: action.isActive } };
    case "unlock":
      return { path: `${base}/unlock`, method: "POST" };
  }
}

export function runUserAction(action: UserAction, idempotencyKey: string): Promise<UserSummary> {
  const { path, method, body } = actionRequest(action);
  return apiFetch<UserSummary>(path, { method, body, idempotencyKey });
}

/** 서버 문구 그대로(§18.4) — 마지막 관리자 보호도 같은 경로. 화면이 지어내지 않는다. */
export function userActionError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.";
}

export const isLastAdminError = (error: unknown): boolean =>
  error instanceof ApiError && error.code === LAST_ADMIN_CODE;
