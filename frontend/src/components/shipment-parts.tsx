// 선적 화면 공용 조각 — 국가 코드 입력·DG 배지·가용재고 '미산정' 배지·거래처 고치기 안내 (S3-2 PR-3b).
// 선적 상세(`routes/shipment-detail.tsx`)와 생성 대화상자(`shipment-create-dialog.tsx`)가 같은 조각을 쓴다 — 표시가 갈리지 않게.

import { useId, type ReactNode } from "react";
import { Link } from "react-router";
import { ApiError } from "../lib/api";
import { ENGLISH_NAME_MISSING_CODE, countryName, type ShipmentDg } from "../lib/shipment";

const inputClass = "rounded border border-gray-300 px-3 py-2 text-sm";

/** 국가 코드 입력 문제 — 비었거나 형식 밖·모르는 코드면 문구(서버는 형식만 보므로 화면이 이름으로 확인한다). */
export function countryProblem(code: string): string | null {
  if (code === "") return "필수 입력입니다.";
  if (!/^[A-Z]{2}$/.test(code)) return "영문 대문자 2자리 국가 코드로 입력해 주세요(예: US).";
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

/** 거래처 영문명·주소 문제(422)는 거래처 화면에서 고친다 — 막다른 길 대신 이동 링크. */
export function PartnerFixHint({ error }: { error: unknown }): ReactNode {
  if (!(error instanceof ApiError)) return null;
  const partnerProblem =
    error.code === ENGLISH_NAME_MISSING_CODE ||
    (error.code === "COMMON.VALIDATION.INVALID_FIELD" && Object.keys(error.detail).some((key) => key.includes("partner_id") || key === "so_id"));
  if (!partnerProblem) return null;
  return (
    <span className="mt-1 block">
      <Link to="/partners" className="underline">
        거래처 화면에서 영문 이름·주소 고치기
      </Link>
    </span>
  );
}

