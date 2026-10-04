# ADR-0081: L/C 대금만기·제시기한·tolerance는 순수 함수와 K 테스트로 고정하고 운영 경로는 UNKNOWN(`LC_TERMS_NOT_REGISTERED`) — tolerance 좁은 쪽 반올림·L/C 플래그 공급 미개방 승계

- **상태**: 자율 확정 — 사후 번복 가능 (S3-2 계획 2026-10-04 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-04
- **관련**: DESIGN.md §7.5·§20(S3-2 [M4] 보강) / WBS S3-2 DoD①·검증 K(v1.6 주석) / ADR-0055 / PROGRESS P-10 / docs/plans/s3-2-plan.md · docs/plans/s3-2/design-integrated.md(§9 적대 검토 정정 우선) — sB §B5·B6, R-11 / 구현 PR-2a

**맥락** — WBS S3-2 DoD "T/T와 L/C 만기 계산 분기 테스트"·검증 K "L/C 제시기한 MIN·tolerance"를 요구하지만 L/C 입력 원천(`lc_terms` — 네고일·인수일·유효기일·tolerance)은 S3-3 산출물이고, S3-1 프로덕션에서 L/C 선택은 닫혀 있다(플래그 행 공급 경로 없음 — P-10). 운영 경로로는 L/C 값을 만들 입력이 없다.

**결정** — ① `trade_docs/schedule.py` 순수 함수(세션·`today_kst`·DB 임포트 0 — 아키텍처 테스트): `lc_payment_due`(SIGHT = 네고일 / USANCE = 인수일+N / 결측 UNKNOWN)·`presentation_deadline`(MIN(B/L+제시일수[기본 21], 유효기일), 결측 UNKNOWN — B/L+21 대체 금지)·`tolerance_bounds`(하한 올림·상한 내림 = 좁은 쪽, 경계 포함). ② **수출·수입 L/C 공통**. ③ 운영 경로 `payment_due`는 LC면 UNKNOWN `LC_TERMS_NOT_REGISTERED`(평가 불능 ≠ 통과). ④ L/C 플래그 공급·tolerance 화면은 열지 않는다(S3-3). ⑤ DoD①·검증 K는 **이 함수의 단위·K 테스트로 충족**한다고 WBS v1.6에 주석. S3-3이 `lc_terms`를 같은 함수에 배선한다.

**근거** — 산식을 지금 고정해 두면 S3-3은 입력 배선만 하면 되고(정의 이원화 방지), L/C를 닫은 채로 두는 것이 S3-1 결정(P-10)과 일관된다. 좁은 쪽 반올림은 허용 범위를 넓히는 오판을 막는다(fail-closed).

**기각한 대안** — S3-2에서 `lc_terms` 선신설(S3-3 범위 침범·화면·권한 동반), DoD를 S3-3으로 이관(WBS 문면 미이행 — 함수는 지금 만들 수 있다), 넓은 쪽·사사오입 반올림(허용 과대), 제시기한 '수출+LC' 한정(문면 구분 없음 — R-11).

**되돌리기 비용** — **낮음**(S3-3 배선 가산). WBS 문면 해석이라 **오너 확인 권장 4순위** — 번복(운영 L/C를 S3-2에서 열기)은 P-10·`lc_terms`를 당겨오는 범위 변경이다.

**부기(2026-10-04 — S3-2 PR-2a 이행: L/C·제시기한·tolerance 순수 함수 — WBS DoD ①·검증 K green)** — `lc_payment_due(tenor, negotiated_on, accepted_on, usance_days)`(SIGHT = 네고일 / USANCE = 인수일+usance[1~365] / 결측 `LC_INPUT_MISSING` / 그 밖 형태 `LC_TENOR_UNSUPPORTED`), `payment_due(…, lc=None)` = **UNKNOWN `LC_TERMS_NOT_REGISTERED`**(운영 경로), `presentation_deadline(bl, expiry_on, presentation_days=21)` = MIN(B/L+N, 유효기일)·결측 `BL_MISSING`/`EXPIRY_MISSING`(B/L+21 대체 금지)·basis는 B/L 승계·N은 1~365 정수, `tolerance_bounds(amount, plus_bp, minus_bp)` = (올림 하한, 내림 상한) 파이썬 정수·bp 0~10000·`within_tolerance` 경계 포함. 지급 형태 열거 `LcTenor{SIGHT, USANCE}`(B/L 기준 usance 등은 S3-3 `lc_terms` 설계 시 가산). GC-A16(L/C 줄)·GC-A17 전 줄을 `golden`·`group_k`로 고정했고 변이(MIN→MAX·+21→+20·반올림 방향 반전 2종·USANCE 기산 바꿔치기·운영 UNKNOWN 해제) 전원 kill. 운영 L/C 배선·플래그 공급은 S3-3 그대로(P-10).

**부기(2026-10-04 — S3-2 PR-4a 이행: 보드 배선)** — 선적 마일스톤 보드가 운영 경로 그대로 배선됐다: L/C 선적의 대금만기 = `payment_due(…, lc=None)` → UNKNOWN `LC_TERMS_NOT_REGISTERED`, 제시기한 행은 L/C 선적에서만 `applicable=true`이고 값은 UNKNOWN `LC_TERMS_NOT_REGISTERED`(B/L+21 대체 금지 — 비L/C 선적은 `applicable=false`·값 null). `lc_terms` 공급·제시기한 산정 배선은 S3-3 그대로(P-10).
