# ADR-0040: certification_status_log는 §17.5 확장 불변 테이블이다

- **상태**: 채택 (S2-2 계획 웹 세션 판정 2026-08-11 — 안건 ④ "기억→CI")
- **날짜**: 2026-08-11
- **관련**: DESIGN.md §17.5(+S2-2 확장 부기)·§5.2("이력 자동")·§3(상태 변경 이력·스냅샷 규율) / audit_log 선례(S0-2) / table_policy.IMMUTABLE_TABLES

**맥락** — §17.5의 명시 불변 3종(stock_movements·확정 분개·audit_log)에 상태 변경 이력이 없다. 이력이 UPDATE 가능하면 "전이 외 변경 거부"의 증거 자체가 조작 가능해진다 — 앱 계약만으로는 기억이고, DB 권한이어야 CI가 지킨다.

**결정** — certification_status_log를 IMMUTABLE_TABLES에 등재하고 마이그레이션에서 revoke_mutations()(앱 계정 INSERT/SELECT만 — UPDATE/DELETE/TRUNCATE 42501 거부, 실측 테스트 동반). 모델은 audit_log 선례 구조(updated_at·deleted_at·version·Actor 믹스인 없음 — 불변 테이블에 "고칠 수 있다"는 신호를 두지 않는다, 행위자=actor_user_id 하나·NULL=시스템). 정정은 원본 수정이 아니라 새 전이 기록이다. DESIGN §17.5에 확장 부기를 명문화했다 — 이후 "상태 변경 이력" 성격 테이블은 신설 세션이 같은 지위로 등재한다.

**근거** — 감사 불변 계보(audit_log)와 같은 위협 모델이고, 인프라(table_policy·revoke_mutations)가 기존재라 비용이 등재 한 줄+호출 한 줄이다.

**기각한 대안** — 앱 계약만(UPDATE 경로 부재+아키텍처 테스트 — DB가 뚫리면 기록이 증거 능력을 잃는다), 금지 트리거(권한 제거로 충분한데 수단이 늘어난다).

**되돌리기 비용** — 낮다(GRANT 복원 마이그레이션 1건). 단 복원하는 순간 §5.2 "이력 자동"의 감사 가치가 소멸한다.

**부기(2026-09-30 — S3-1 계획)** — (자율 확정 — 사후 번복 가능. 위 원문 결정은 고치지 않는다.) **§17.5 확장 등재 8테이블** — 상태이력 4표(`quotation_status_log`·`proforma_invoice_status_log`·`sales_order_status_log`·`purchase_order_status_log`)·`approval_events`·`gate_evaluations`·`gate_overrides`·`payments`를 이 ADR의 서식(`IMMUTABLE_TABLES`+`revoke_mutations` 수기 호출, downgrade는 `drop_table`이 REVOKE 소멸)으로 등재한다. 또 **컬럼 단위 UPDATE 권한 수단**을 추가한다: 일부 컬럼만 상태·결정·소비·종료로 갱신되는 `approvals`·`approval_lines`·`delegations`는 `restrict_update_columns`(`REVOKE UPDATE, DELETE, TRUNCATE`+허용 컬럼 `GRANT UPDATE`)와 `COLUMN_UPDATE_ALLOWLIST`로 스냅샷·당사자·기간 컬럼을 INSERT 이후 DB가 불변으로 강제한다(트리거는 여전히 쓰지 않는다). GRANT는 autogenerate가 못 보므로 마이그레이션에서 수기 호출하고 `has_column_privilege` 실측 테스트를 둔다. 근거: ADR-0051·0060·0063·0064 / design-B.md B6, design-C.md C1·C6.
