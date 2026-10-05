# ADR-0093: 서류 렌더링 — 서버 내 PDF(ReportLab ≥ 3.6.13)·XLSX(openpyxl)·동봉 폰트·템플릿 판 동결·외부 호출 0·산출물 저장(documents FILE + 불변 renditions, 첫 파일 정본)·생성물 삭제 잠금·생성물 다운로드 역할 축소 + audit·QT·PI 운영 개방·주입 방어

- **상태**: 자율 확정 — 사후 번복 가능 (S3-3 계획 2026-10-05 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-05
- **관련**: DESIGN.md §7.6·§4.7·§17.4·§18.1(S3-3 [M4] 보강) / WBS S3-3 산출물 'QT·PI·CI·PL·S/I 템플릿 렌더링(PDF·엑셀·언어 변형)'(v1.7 주석 ①·⑪) / ADR-0028·0029·0049 / docs/plans/s3-3-plan.md · docs/plans/s3-3/design-integrated.md(§9 적대 검토 정정 R-01~R-40 우선) — sA §A12·§A13, sC C7, X-02·X-24, N-03·N-10, R-03·R-11·R-14·R-27·R-28·R-30·R-33·R-36 / 구현 PR-3b(M19)·PR-5a·5b(CI·PL·S/I 단계)

**맥락** — §7.6은 한 원천에서 PDF·엑셀 렌더링을 요구한다. 부록 A는 산출물 저장(documents + 불변 연결), 부록 C는 비저장·결정적 재렌더를 제안해 충돌했다(X-02). ReportLab `Paragraph`는 입력을 자체 마크업으로 해석해 로컬 파일·SSRF·CVE-2023-33733 표면이 있다(R-03). 리포에 렌더 의존성이 없다.

**결정** — ① 렌더는 서버 안·트랜잭션 밖(파일 IO — ADR-0049): PDF = ReportLab(invariant), XLSX = openpyxl(사람 입력 = 문자열 셀 — 수식 주입 방어), 동봉 NanumGothic(OFL·sha256 고정)·템플릿 판 digest 동결·글리프 미지원 문자 BLOCK, **외부 호출 0**. ② 의존성 = `reportlab`·`openpyxl` `==` 고정(**reportlab ≥ 3.6.13 단언**), 임포트는 `doc_render`만(순수 — DB·네트워크·`now`·float 0), `tzdata==2026.5` 줄 diff 0(R-4a-9 발동 — R-36). ③ **주입 방어**: 사람 입력 문자열 단일 이스케이프 `markup_safe`, `Paragraph` 생성 1곳(AST), `rl_config` 신뢰 스킴·호스트 비움(원격 리소스 로드 차단), 렌더 입력 뷰 = 허용 필드 고정(원가 키·`internal_note`·담당자·감사 행위자 0 — R-28). ④ **산출물 저장**: documents FILE + IMMUTABLE `trade_document_renditions`(UNIQUE (원천·종류·형식·언어)) — **첫 파일이 정본**(재출력 = 같은 바이트), 경쟁 렌더 = 유일 위반 → 승자 반환·내 파일 삭제·고아는 storage-monitor 보고. 멱등 지문 = **요청 본문만**(파일 해시 제외 — R-27). ⑤ **생성물 삭제 = 역할 무관 409** `GENERATED_LOCKED`, **생성물 다운로드** = QT·PI A·T / CI·PL·S/I A·T·L, 매번 audit `documents.generated.downloaded` 1행(업무 데이터 쓰기 0 — 감사 1행 예외, R-30), 다운로드 통로는 기존 `GET /documents/{id}/download` 하나(attachment). 생성물 판정 = 해석기 레지스트리(미등록 = 403). ⑥ **QT·PI 렌더 운영 개방**(`POST /quotations/{id}/render`·`/proforma-invoices/{id}/render`, A·T) — 게이트 = 원천 **`frozen_at IS NOT NULL`**(DRAFT·초안 폐기 취소 QT 409 `RENDITION.SOURCE_NOT_FROZEN` — R-14). ⑦ 언어 = QT·PI EN·KO, CI·PL·S/I EN만(422 `LANGUAGE_NOT_SUPPORTED` — R-33). ⑧ 무상 표기 상수 `FREE_OF_CHARGE_TEXT` 단일 출처('NO COMMERCIAL VALUE'). ⑨ 렌더·다운로드는 outbox 이벤트 0, 대외 발송 0.

**근거** — '보낸 그 파일' 증명·백업 세트·sha256 검증·고아 탐지가 저장형에서만 성립한다(`D:461`). 재렌더 정본은 라이브러리·폰트 판이 바뀌면 과거 서류가 달라진다. 계좌번호가 담긴 대외 전달물은 다운로드를 좁히고 감사한다.

**기각한 대안** — 비저장·결정적 재렌더(판 변경 시 과거 서류 변형 — X-02), 외부 렌더 서비스·헤드리스 브라우저(외부 호출·의존성 표면), 템플릿 엔진(Jinja) HTML→PDF(마크업 주입 표면 확대), XLSX 바이트 해시 멱등(비결정성 — R-27), 생성물 삭제 허용(정본 소실), 다운로드 전 역할(계좌번호 유출 채널).

**되돌리기 비용** — **중간** — 저장 → 비저장 전환은 쌓인 정본 파일 보존 의무 때문에 단방향(파일은 지우지 않는다). 다운로드 역할은 낮음(넓히기 행 1줄).
