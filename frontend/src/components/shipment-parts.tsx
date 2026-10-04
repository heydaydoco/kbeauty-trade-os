// 선적 화면 공용 조각 — 국가 코드 입력·DG 배지·가용재고 '미산정' 배지·거래처 고치기 안내 (S3-2 PR-3b).
// 선적 상세(`routes/shipment-detail.tsx`)와 생성 대화상자(`shipment-create-dialog.tsx`)가 같은 조각을 쓴다 — 표시가 갈리지 않게.

import { useId } from "react";
import { Link } from "react-router";
import { ApiError } from "../lib/api";
import { hasRole, useSession } from "../lib/session";
import { ENGLISH_NAME_MISSING_CODE, canonicalCountryOf, countryName, type ShipmentDg } from "../lib/shipment";

const inputClass = "rounded border border-gray-300 px-3 py-2 text-sm";

/** 국가 코드 입력 문제 — 비었거나 형식 밖·모르는 코드면 문구(서버는 형식만 보므로 화면이 이름으로 확인한다). */
export function countryProblem(code: string): string | null {
  if (code === "") return "필수 입력입니다.";
  if (!/^[A-Z]{2}$/.test(code)) return "영문 대문자 2자리 국가 코드로 입력해 주세요(예: US).";
  // CLDR 별칭(UK·DD·SU 등)은 이름이 붙어도 ISO 정식 코드가 아니다 — 정식 코드를 안내한다(서버는 형식만 본다).
  const canonical = canonicalCountryOf(code);
  if (canonical !== null) return `${code}는 ISO 정식 코드가 아닙니다 — ${canonical}로 입력해 주세요.`;
  if (countryName(code) === null) return "알 수 없는 국가 코드입니다(ISO 3166-1 두 자리 — 예: US·JP·CN).";
  return null;
}

export function CountryInput({
  label,
  value,
  onChange,
  problem,
  showProblem = true,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  problem: string | null;
  showProblem?: boolean;
}) {
  const name = countryName(value);
  const inputId = useId();
  const hintId = useId();
  return (
    <div className="flex flex-col gap-1 text-sm">
      <label htmlFor={inputId} className="cell-nowrap text-gray-600">
        {label} (필수)
      </label>
      <input
        id={inputId}
        value={value}
        maxLength={2}
        aria-required="true"
        aria-invalid={showProblem && problem !== null}
        aria-describedby={hintId}
        placeholder="예: US"
        onChange={(event) => onChange(event.target.value.toUpperCase().replace(/[^A-Z]/g, ""))}
        className={`${inputClass} w-24 text-center`}
      />
      <span id={hintId} className="break-keep text-xs">
        {name !== null ? (
          <span className="cell-nowrap text-gray-600">{name}</span>
        ) : showProblem && problem !== null ? (
          <span role="alert" className="text-signal-red">
            {problem}
          </span>
        ) : (
          <span className="text-gray-400">국가 코드 2자리</span>
        )}
      </span>
    </div>
  );
}

export function AvailabilityBadge() {
  return (
    <span className="cell-nowrap rounded border border-gray-300 bg-gray-100 px-1.5 py-0.5 text-xs text-gray-600">가용재고 미산정</span>
  );
}

export function DgBadge({ dg }: { dg: ShipmentDg }) {
  if (!dg.flag) return <span className="text-gray-400">—</span>;
  const detail = [dg.un_number, dg.dg_class].filter((part): part is string => part !== null && part !== "").join(" · ");
  return (
    <span className="cell-nowrap">
      <span className="rounded border border-signal-red px-1.5 py-0.5 text-xs text-signal-red">DG</span>
      {detail !== "" && <span className="ml-1 text-xs">{detail}</span>}
    </span>
  );
}

/**
 * 거래처 영문명 결측(422 `ENGLISH_NAME_MISSING`)의 고치는 길 — 거래처 수정 화면·API가 없다(부채 P-44). 실재하는 경로는
 * **거래처 CSV 내보내기 → 영문명·영문주소 채우기 → 엑셀 임포트(대상: 거래처)**이고 임포트는 무역·관리자 전용이다(runbook 운영 개시 ⑤).
 * 그 밖의 역할에는 링크 없이 요청 안내만. ★ 같은 `INVALID_FIELD`(422)가 주소의 보이지 않는 글자·거래처 유형 불일치·삭제 거래처에
 * 함께 쓰여 코드로 가를 수 없으므로 INVALID_FIELD에는 아무것도 붙이지 않는다(서버 문구만 — 주소 전용 코드 분리는 부채 R-3b-7).
 */
export function PartnerFixHint({ error }: { error: unknown }) {
  const { me } = useSession();
  if (!(error instanceof ApiError) || error.code !== ENGLISH_NAME_MISSING_CODE) return null;
  if (!hasRole(me, "TRADE")) {
    return <span className="mt-1 block break-keep">무역 담당에게 거래처 영문명 등록을 요청하세요.</span>;
  }
  return (
    <span className="mt-1 block break-keep">
      거래처 영문명은{" "}
      <Link to="/partners" className="underline">
        거래처
      </Link>{" "}
      &lsquo;CSV 내보내기&rsquo;로 받아 영문명·영문주소를 채운 뒤{" "}
      <Link to="/imports" className="underline">
        엑셀 임포트
      </Link>
      (대상: 거래처)로 올려 고칩니다.
    </span>
  );
}
