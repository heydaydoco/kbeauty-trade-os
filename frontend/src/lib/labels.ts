// 코드값 → 한국어 라벨 (DESIGN.md §14 한국어 UI / §18.4 에러 메시지 이원화).
//
// ★ 서버는 코드 그대로 준다(ACTIVE / SINGLE). 라벨을 서버가 붙이면 CSV·API·화면이
//   같은 값을 다르게 부르게 되고, S1-3의 엑셀 왕복 편집(§12.2)에서 되돌릴 수
//   없다. 번역은 화면의 일이고, 그 표는 여기 한 곳에만 둔다.

const STATUS: Record<string, string> = {
  ACTIVE: "판매중",
  DISCONTINUED: "단종",
};

const KIND: Record<string, string> = {
  SINGLE: "단품",
  SET: "세트",
};

/** 모르는 코드는 감추지 않고 그대로 보여 준다 — 빈칸은 원인 추적을 막는다. */
function translate(table: Record<string, string>, code: string): string {
  return table[code] ?? code;
}

const RULE_TYPE: Record<string, string> = {
  PROHIBITED: "금지",
  RESTRICTED: "제한",
};

// 스크리닝 3분류 (§4.3). ★ "적합/통과/합격" 류의 판정 워딩을 여기 추가하지
// 않는다 — 스크리닝은 판정이 아니다(웹 세션 판정 D-②, 테스트가 부재를 지킨다).
const CLASSIFICATION: Record<string, string> = {
  PROHIBITED: "금지",
  OVER_LIMIT: "제한초과",
  UNLISTED: "미등재",
};

// 자재 유형 (§4.4).
const MATERIAL_TYPE: Record<string, string> = {
  BULK: "벌크",
  RAW_MATERIAL: "원료",
  CONTAINER: "용기",
  CARTON: "단상자",
  LABEL: "라벨",
  AUX: "부자재",
};

// BOM 라인의 원산지 상태 (§4.4). 미상은 판정 시 역외로 간주된다(S3-4).
const ORIGIN_STATUS: Record<string, string> = {
  DOMESTIC: "역내",
  FOREIGN: "역외",
  UNKNOWN: "미상",
};

// 라벨 승인 상태 (§4.5). 사용자가 정하는 워크플로 상태다 — 시스템 판정이 아니다.
const APPROVAL_STATUS: Record<string, string> = {
  DRAFT: "초안",
  APPROVED: "승인",
  RETIRED: "폐기",
};

// 요건 템플릿 적용단위 (§5.1 — WBS S2-1 DoD 5종 문면 그대로).
const APPLIES_TO: Record<string, string> = {
  PRODUCT: "처방",
  SKU: "SKU",
  FACILITY: "시설",
  COMPANY: "기업",
  INGREDIENT: "성분",
};

// 요건 템플릿 상태 (§5.5 — ADR-0033). "확정"은 요건 정의의 등록 완료 표시이지
// 어떤 대상의 적합 판정이 아니다 — 판정 워딩을 여기 추가하지 않는다(ADR-0022).
const TEMPLATE_STATUS: Record<string, string> = {
  DRAFT: "초안",
  CONFIRMED: "확정",
  RETIRED: "폐기",
};

// 거래처 유형 (§4.6 열거 10종 — ADR-0026).
const PARTNER_TYPE: Record<string, string> = {
  SUPPLIER: "공급사",
  OEM: "OEM",
  BUYER: "바이어",
  RP: "RP",
  IOR: "IOR",
  DOMESTIC_RP: "경내책임자",
  FORWARDER: "포워더",
  CUSTOMS_BROKER: "관세사",
  THREE_PL: "3PL",
  CERT_AGENCY: "인증대행",
};

// 문서 소유자 유형 (§4.7 — ADR-0028, 소비분 2종 한정).
const DOCUMENT_OWNER_TYPE: Record<string, string> = {
  SKU: "SKU",
  LABEL: "라벨",
};

// 문서 저장 형태 (§4.7 — FILE/LINK 2형).
const STORAGE_KIND: Record<string, string> = {
  FILE: "파일",
  LINK: "링크",
};

// 임포트 스테이징 상태 (§12.2). 확정은 사람 1클릭 — 자동 확정이 없다(§15 L2).
const IMPORT_STATUS: Record<string, string> = {
  PENDING: "검토 대기",
  CONFIRMED: "확정",
};

// 임포트 행 분류 (§12.2 — diff 결과 3종. 무변경 행은 스테이징되지 않는다).
const IMPORT_ROW_KIND: Record<string, string> = {
  NEW: "신규",
  CHANGED: "변경",
  ERROR: "오류",
};

export const statusLabel = (code: string): string => translate(STATUS, code);
export const kindLabel = (code: string): string => translate(KIND, code);
export const ruleTypeLabel = (code: string): string => translate(RULE_TYPE, code);
export const classificationLabel = (code: string): string => translate(CLASSIFICATION, code);
export const materialTypeLabel = (code: string): string => translate(MATERIAL_TYPE, code);
export const originStatusLabel = (code: string): string => translate(ORIGIN_STATUS, code);
export const approvalStatusLabel = (code: string): string => translate(APPROVAL_STATUS, code);
export const appliesToLabel = (code: string): string => translate(APPLIES_TO, code);
export const templateStatusLabel = (code: string): string => translate(TEMPLATE_STATUS, code);
export const partnerTypeLabel = (code: string): string => translate(PARTNER_TYPE, code);
export const documentOwnerTypeLabel = (code: string): string =>
  translate(DOCUMENT_OWNER_TYPE, code);
export const storageKindLabel = (code: string): string => translate(STORAGE_KIND, code);
export const importStatusLabel = (code: string): string => translate(IMPORT_STATUS, code);
export const importRowKindLabel = (code: string): string => translate(IMPORT_ROW_KIND, code);

/** 값이 없을 때 표에 넣는 표시. 빈칸은 "누락"과 "0"을 구분하지 못한다. */
export const EMPTY = "—";

export function orEmpty(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return EMPTY;
  return String(value);
}
