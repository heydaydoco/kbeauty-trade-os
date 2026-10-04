// 휴일 캘린더 (S3-2 PR-2b — design-D §D2-3 H1~H5·§D5·§D13 / 서버 계약: PROGRESS 'S3-2 PR-2a' PR-2b 인계 계약).
//
// 권한(백엔드 authz_matrix와 일치): 열람·CSV 내려받기 = **전 역할**, 편집(H3)·CSV 미리보기(H5) = **ADMIN 전용**.
// 비관리자에게는 편집 UI를 그리지 않는다 — 화면 게이트는 편의일 뿐 서버가 403으로 다시 막는다(§18.1).
// ★ `calendar === null`(미선언)은 '휴일 캘린더 미등록 — 확인 불가'(판정 UNVERIFIED, 평일로 보지 않음)이고,
//   선언 + 0건('휴일 없음 확인')과 다르게 그린다.
// ★ 날짜(`holiday_on`·`verified_on`)는 문자열 그대로 보인다(Date 해석 금지 — 하루 밀림). `updated_at`만 KST 시각.
// ★ 선적 상세의 UNVERIFIED 배지 링크(`/holidays?country=CN&year=2027`, PR-4b)가 이 화면의 진입점이라 선택은 주소에 둔다.
// 규약: 한국어 break-keep · 국가 코드·날짜·건수 nowrap+가운데 정렬 · 표는 섹션 안 overflow-x-auto(390px 페이지 가로 스크롤 0).

import { useState } from "react";
import { useSearchParams } from "react-router";
import { HolidayCalendarDialog } from "../components/holiday-calendar-dialog";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { errorMessage } from "../lib/api-errors";
import { toKstDisplay, todayKst } from "../lib/datetime";
import { downloadFile } from "../lib/download";
import {
  HOLIDAYS_QUERY_KEY,
  calendarsPath,
  exportFallbackName,
  exportPath,
  holidaysListPath,
  isCountryCode,
  isDeclarableYear,
  type CalendarYear,
  type Holiday,
  type HolidayPage,
} from "../lib/holidays";
import { usePagedList } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";

export function HolidaysPage() {
  const { me } = useSession();
  const isAdmin = hasRole(me); // 편집·CSV 미리보기는 ADMIN 전용
  const [params, setParams] = useSearchParams();
  const country = params.get("country") ?? "";
  const year = params.get("year") ?? "";
  const selected = isCountryCode(country) && isDeclarableYear(year);

  const [draftCountry, setDraftCountry] = useState(country);
  const [draftYear, setDraftYear] = useState(year || todayKst().slice(0, 4));
  const [filterError, setFilterError] = useState<{ country: string | null; year: string | null }>({ country: null, year: null });
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState<CalendarYear | null>(null);
  // 주소가 밖에서 바뀌면(배지 링크·뒤로 가기) 입력칸도 따라간다 — 렌더 단계 보정(effect 지연 없음).
  const [shown, setShown] = useState({ country, year });
  if (shown.country !== country || shown.year !== year) {
    setShown({ country, year });
    setDraftCountry(country);
    if (year) setDraftYear(year);
    setEditing(false);
  }

  function select(nextCountry: string, nextYear: string) {
    setDraftCountry(nextCountry);
    setDraftYear(nextYear);
    setFilterError({ country: null, year: null });
    setSaved(null);
    setParams({ country: nextCountry, year: nextYear });
  }

  function apply() {
    const nextCountry = draftCountry.trim();
    const nextYear = draftYear.trim();
    const problems = {
      country: isCountryCode(nextCountry) ? null : "국가는 영문 대문자 2자(예: CN, US)로 입력해 주세요.",
      year: isDeclarableYear(nextYear) ? null : "연도는 2000~2999 사이 4자리 숫자로 입력해 주세요.",
    };
    if (problems.country || problems.year) {
      setFilterError(problems);
      return;
    }
    select(nextCountry, nextYear);
  }

  // 선언 목록(H1) — 국가를 고르면 그 국가의 전 연도, 아니면 전체.
  const calendars = usePagedList<CalendarYear>(
    [...HOLIDAYS_QUERY_KEY, "calendars", selected ? country : ""],
    calendarsPath(selected ? country : "", ""),
  );

  return (
    <section>
      <header>
        <h1 className="text-2xl font-bold">휴일 캘린더</h1>
        <p className="mt-1 break-keep text-sm text-gray-500">
          국가·연도별 공휴일입니다. 선적 일정(ETA)의 휴일 경고가 이 자료로 판정됩니다. 캘린더가 등록되지 않은 국가·연도는 '확인 불가'로
          표시되며 평일로 간주하지 않습니다.
          {!isAdmin && " 등록·편집은 관리자만 할 수 있습니다."}
        </p>
      </header>

      <form
        role="search"
        aria-label="국가·연도 선택"
        noValidate
        className="mt-4 flex flex-wrap items-start gap-3 text-sm"
        onSubmit={(event) => {
          event.preventDefault();
          apply();
        }}
      >
        <div className="flex flex-col gap-1">
          <label htmlFor="holiday-country" className="cell-nowrap text-gray-600">
            국가 코드
          </label>
          <input
            id="holiday-country"
            value={draftCountry}
            maxLength={2}
            autoCapitalize="characters"
            placeholder="CN"
            aria-invalid={filterError.country !== null}
            aria-describedby={filterError.country ? "holiday-country-error" : undefined}
            onChange={(event) => setDraftCountry(event.target.value.toUpperCase())}
            className="cell-nowrap w-24 rounded border border-gray-300 px-3 py-2 text-center"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="holiday-year" className="cell-nowrap text-gray-600">
            연도
          </label>
          <input
            id="holiday-year"
            value={draftYear}
            maxLength={4}
            inputMode="numeric"
            aria-invalid={filterError.year !== null}
            aria-describedby={filterError.year ? "holiday-year-error" : undefined}
            onChange={(event) => setDraftYear(event.target.value)}
            className="num w-24 rounded border border-gray-300 px-3 py-2 text-center"
          />
        </div>
        <button type="submit" className="cell-nowrap mt-6 rounded bg-gray-900 px-4 py-2 text-white">
          조회
        </button>
        {(filterError.country || filterError.year) && (
          <div role="alert" className="w-full break-keep text-xs text-signal-red">
            {filterError.country && <p id="holiday-country-error">{filterError.country}</p>}
            {filterError.year && <p id="holiday-year-error">{filterError.year}</p>}
          </div>
        )}
      </form>

      {saved && (
        <p role="status" className="mt-4 break-keep rounded border border-gray-300 p-2 text-sm">
          {saved.country_code} {saved.year} 휴일 캘린더를 저장했습니다(<span className="num">{saved.holiday_count}</span>건).
        </p>
      )}

      {selected && (
        <HolidayYearSection
          country={country}
          year={year}
          isAdmin={isAdmin}
          onEdit={() => {
            setSaved(null);
            setEditing(true);
          }}
        />
      )}

      <section aria-labelledby="holiday-calendars-heading" className="mt-8">
        <h2 id="holiday-calendars-heading" className="text-lg font-semibold">
          등록된 캘린더 {selected ? <span className="cell-nowrap">({country} 전 연도)</span> : "(전체)"}
        </h2>
        <ListPager data={calendars.data} page={calendars.page} onPageChange={calendars.setPage} className="mt-2" />
        <div className="mt-2 overflow-x-auto rounded-lg border border-gray-200">
          <ListState
            isPending={calendars.isPending}
            error={calendars.error}
            isEmpty={calendars.data?.items.length === 0}
            emptyHint={
              selected
                ? `${country}에 등록된 휴일 캘린더가 없습니다.${isAdmin ? " 위에서 연도를 골라 등록하세요." : " 관리자에게 등록을 요청하세요."}`
                : isAdmin
                  ? "등록된 휴일 캘린더가 없습니다. 국가·연도를 골라 등록하세요."
                  : "등록된 휴일 캘린더가 없습니다. 관리자에게 등록을 요청하세요."
            }
          >
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-600">
                <tr>
                  <th scope="col" className="cell-nowrap px-3 py-2 text-center">
                    국가
                  </th>
                  <th scope="col" className="cell-nowrap px-3 py-2 text-center">
                    연도
                  </th>
                  <th scope="col" className="cell-nowrap px-3 py-2 text-center">
                    휴일 수
                  </th>
                  <th scope="col" className="cell-nowrap px-3 py-2 text-center">
                    확인일
                  </th>
                  <th scope="col" className="cell-nowrap px-3 py-2 text-left">
                    수정
                  </th>
                  <th scope="col" className="cell-nowrap px-3 py-2 text-center">
                    보기
                  </th>
                </tr>
              </thead>
              <tbody>
                {calendars.data?.items.map((row) => (
                  <tr key={row.id} className="border-t border-gray-100">
                    <td className="cell-nowrap px-3 py-2 text-center">{row.country_code}</td>
                    <td className="num cell-nowrap px-3 py-2 text-center">{row.year}</td>
                    <td className="num cell-nowrap px-3 py-2 text-center">{row.holiday_count}</td>
                    <td className="num cell-nowrap px-3 py-2 text-center">{row.verified_on}</td>
                    <td className="break-keep px-3 py-2 text-xs text-gray-600">
                      {row.updated_by_name ?? "-"} · <span className="cell-nowrap">{toKstDisplay(row.updated_at)}</span>
                    </td>
                    <td className="cell-nowrap px-3 py-2 text-center">
                      <button
                        type="button"
                        aria-label={`${row.country_code} ${row.year} 보기`}
                        onClick={() => select(row.country_code, String(row.year))}
                        className="underline"
                      >
                        보기
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ListState>
        </div>
      </section>

      {isAdmin && editing && selected && (
        <HolidayCalendarDialog
          country={country}
          year={year}
          onClose={() => setEditing(false)}
          onSaved={(calendar) => {
            setEditing(false);
            setSaved(calendar);
          }}
        />
      )}
    </section>
  );
}

function HolidayYearSection({
  country,
  year,
  isAdmin,
  onEdit,
}: {
  country: string;
  year: string;
  isAdmin: boolean;
  onEdit: () => void;
}) {
  const list = usePagedList<Holiday, HolidayPage>([...HOLIDAYS_QUERY_KEY, "list", country, year], holidaysListPath(country, year));
  const [csvError, setCsvError] = useState<string | null>(null);
  const calendar = list.data?.calendar ?? null;

  async function exportCsv() {
    setCsvError(null);
    try {
      await downloadFile(exportPath(country, year), exportFallbackName(country, year));
    } catch (caught) {
      setCsvError(errorMessage(caught, "내려받지 못했습니다."));
    }
  }

  return (
    <section aria-labelledby="holiday-year-heading" className="mt-6">
      <h2 id="holiday-year-heading" className="text-lg font-semibold">
        <span className="cell-nowrap">
          {country} {year}
        </span>{" "}
        휴일
      </h2>
      <ListState isPending={list.isPending} error={list.error} isEmpty={false} emptyHint={null}>
        {list.data && calendar === null && (
          <div className="mt-3 rounded border border-gray-400 bg-gray-50 p-3 text-sm">
            <p className="font-semibold">
              <span className="rounded bg-gray-600 px-2 py-0.5 text-xs text-white">미등록</span> 휴일 캘린더 미등록 — 확인 불가{" "}
              <span className="cell-nowrap">
                ({country} {year})
              </span>
            </p>
            <p className="mt-1 break-keep text-gray-600">
              이 국가·연도의 휴일이 아직 등록되지 않아 선적 일정의 휴일 여부를 판정할 수 없습니다(평일로 간주하지 않습니다).
              {isAdmin ? " 근거 링크와 함께 휴일을 등록하세요. 휴일이 없는 해라면 빈 목록으로 등록하면 됩니다." : " 관리자에게 등록을 요청하세요."}
            </p>
            {isAdmin && (
              <button type="button" onClick={onEdit} className="cell-nowrap mt-3 rounded bg-gray-900 px-3 py-2 text-white">
                휴일 캘린더 등록
              </button>
            )}
          </div>
        )}
        {list.data && calendar !== null && (
          <div className="mt-3 flex flex-col gap-3 text-sm">
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
              <dt className="cell-nowrap text-gray-500">근거 링크</dt>
              <dd className="min-w-0 break-all">
                <a href={calendar.source_url} target="_blank" rel="noopener noreferrer" className="underline">
                  {calendar.source_url}
                </a>
              </dd>
              <dt className="cell-nowrap text-gray-500">확인일</dt>
              <dd className="num cell-nowrap">{calendar.verified_on}</dd>
              <dt className="cell-nowrap text-gray-500">휴일 수</dt>
              <dd className="num cell-nowrap">{calendar.holiday_count}건</dd>
              <dt className="cell-nowrap text-gray-500">마지막 수정</dt>
              <dd className="break-keep">
                {calendar.updated_by_name ?? "-"} · <span className="cell-nowrap">{toKstDisplay(calendar.updated_at)}</span>
              </dd>
            </dl>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void exportCsv()}
                className="cell-nowrap rounded border border-gray-300 px-3 py-2"
              >
                CSV 내보내기
              </button>
              {isAdmin && (
                <button type="button" onClick={onEdit} className="cell-nowrap rounded bg-gray-900 px-3 py-2 text-white">
                  편집·CSV 불러오기
                </button>
              )}
            </div>
            {csvError && (
              <p role="alert" className="break-keep text-signal-red">
                {csvError}
              </p>
            )}
            {list.data.total === 0 ? (
              <p className="break-keep rounded border border-gray-200 p-3">
                <strong>휴일 없음 확인</strong> — 이 연도에는 휴일이 없다고 확인되었습니다(근거 링크·확인일 위 참조).
              </p>
            ) : (
              <>
                <ListPager data={list.data} page={list.page} onPageChange={list.setPage} />
                <div className="overflow-x-auto rounded-lg border border-gray-200">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 text-gray-600">
                      <tr>
                        <th scope="col" className="cell-nowrap px-3 py-2 text-center">
                          날짜
                        </th>
                        <th scope="col" className="cell-nowrap px-3 py-2 text-left">
                          휴일 이름
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {list.data.items.map((holiday) => (
                        <tr key={holiday.id} className="border-t border-gray-100">
                          <td className="num cell-nowrap px-3 py-2 text-center">{holiday.holiday_on}</td>
                          <td className="break-keep px-3 py-2">{holiday.name}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        )}
      </ListState>
    </section>
  );
}
