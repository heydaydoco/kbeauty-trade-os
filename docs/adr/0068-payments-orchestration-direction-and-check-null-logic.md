# ADR-0068: 입금 기록·역기록 오케스트레이션은 trade_chain이 맡는다(방향 반전) — payments는 순수 원장, CHECK의 NULL 3값 논리는 명시 가드

- **상태**: 자율 확정(2026-10-01) — 사후 번복 가능 (S3-1 PR-10a — PROGRESS 9a "자율 확정"의 구현 확정)
- **날짜**: 2026-10-01
- **관련**: DESIGN.md §2.8(모듈 계층 DAG)·§7.3·§7.10 / ADR-0052·0064 / docs/plans/s3-1/design-E.md E7, design-integrated.md §2.8·X-17·X-21

**맥락** — 설계는 `payments`가 입금·역기록 때 PI를 잠그고 `converge_payment_status`를 부르라고 하지만(E7), 두 함수는 L2 `trade_chain`에 있고 §2.8은 payments→trade_chain 임포트를 금지한다(L0 하향은 L0→L1 금지로 불가, DAG 예외는 §2.8 취지 훼손). 또한 설계의 CHECK `kind_sign`은 REVERSAL 분기를 `char_length(btrim(reason)) >= 2`로만 적어, 사유가 NULL이면 식이 NULL이 되어(3값 논리) **CHECK가 통과**한다.

**결정** — ① 입금·역기록 **오케스트레이션**(멱등 선점 → PI `lock_chain` 단일 진입 → 원장 함수 → `converge_payment_status` → 완료 기록)을 `trade_chain/payment_flow.py`에 두고 `payments`는 검증·INSERT·audit·outbox만 하는 **순수 원장**(잠금·멱등·수렴·트랜잭션 개설 없음 — 스캔 테스트)으로 둔다. 쓰기 라우트는 trade_chain 라우터에, 조회 라우트는 payments 라우터에 둔다. `converge_payment_status` 호출처는 오케스트레이터 1곳(핀). ② `kind_sign`의 REVERSAL 분기에 `reason IS NOT NULL`을 명시한다(M08 최초 작성 시 반영 — 마이그레이션이 아직 미병합이라 별도 보정 마이그레이션 없음).

**근거** — 호출 방향만 뒤집으면 두 모듈의 의존·잠금 순서(멱등→QT→PI→원장)가 설계와 동일하다. 순수 원장은 S3-3(채권 입금)이 같은 함수군을 재사용하기 쉽다. NULL 가드는 "사유 없는 역기록 거부"(E7 테스트 요건)가 실제로 DB에서 성립하게 한다(원시 INSERT 테스트가 구멍을 잡았다).

**기각한 대안** — `lock_chain`·`converge`를 L0로 내림(L0→L1 금지), payments를 trade_chain 상위로 승격(L2 상호 의존 사이클), DAG 예외 ADR, 서비스 계층 검증만으로 사유 필수 유지(DB 백스톱 상실).

**되돌리기 비용** — 낮음. 오케스트레이터·순수 원장 경계는 함수 이동 수준이고(테스트가 잠금·호출 순서를 고정), CHECK 가드는 신규 테이블이라 배포 전 무비용(배포 뒤 변경은 CHECK 재정의 마이그레이션 1건).

**부기(2026-10-05 — S3-3 계획)** — (자율 확정 — 사후 번복 가능. 위 원문 결정은 고치지 않는다.) **역기록 오케스트레이션 순서 개정(S3-3 PR-2d — 적대 R-20·R-34, ADR-0089·0097)**: 위 ① 'PI `lock_chain` 단일 진입'을 **peek → 대상 판별(PI·채권) → 거래처 여신 잠금(`lock_buyer_for_credit` — 역기록은 노출 증가) → 대상별 잠금**(PI: `lock_chain(PI)` / 채권: PI `FOR SHARE`(있으면) → SO `lock_document(read=True)` → receivables `FOR UPDATE` — `lock_chain` 금지)으로 바꾼다. **PI 입금도 거래처 잠금을 먼저 잡는다**(멱등 → partners NKU → `lock_chain(PI)` — 같은 송금의 채권·PI 교차 경합에서 이중 입력 판정 창 0). `payment_flow.py:6` 독스트링 '거래처→QT→PI'는 실측(QT→PI)에 맞춘다. payments는 계속 순수 원장(잠금·멱등·수렴 0)이고 채권 입금 오케스트레이션도 `trade_chain`에 둔다. ②의 NULL 3값 논리 명시 가드 원칙은 M16b의 새 CHECK(`num_nonnulls(pi_id, receivable_id) = 1` 등)에도 적용하고 기존 CHECK는 무변경이다.
