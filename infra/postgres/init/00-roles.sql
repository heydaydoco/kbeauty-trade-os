-- ============================================================================
-- 00-roles.sql — 런타임/마이그레이션 역할 분리 (멱등)
-- 실행 주체: postgres 슈퍼유저 (compose의 db-init one-shot 서비스)
-- 근거: DESIGN.md §17.5, §18.1 / GC-A4 / ADR-0002
--
--   kbos_owner : 스키마 소유자. alembic 마이그레이션 전용. DDL 가능.
--   kbos_app   : 런타임 계정. public 스키마에 CREATE 권한 없음.
--
-- 왜 나누는가 — PostgreSQL에서 테이블 소유자는 자신의 권한을 REVOKE로 뺏을 수 없고
-- 금지 트리거도 스스로 DISABLE할 수 있다. 앱이 소유자인 한 §17.5의
-- "stock_movements·확정 분개·audit_log는 UPDATE/DELETE 불가(DB 권한으로 강제)"와
-- GC-A4의 "DB 권한 레벨에서 차단"은 성립하지 않는다.
-- ============================================================================

\set ON_ERROR_STOP on

-- 역할 생성 (이미 있으면 건너뜀)
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE', 'kbos_owner')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kbos_owner')
\gexec

SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE', 'kbos_app')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kbos_app')
\gexec

-- 비밀번호는 매 기동마다 .env 값으로 동기화한다(멱등 — .env를 고치면 재기동으로 반영).
ALTER ROLE kbos_owner WITH PASSWORD :'owner_pw';
ALTER ROLE kbos_app   WITH PASSWORD :'app_pw';

-- ── 세션 기본값 ──────────────────────────────────────────────────────────
-- 시각은 UTC 저장이 원칙(§2 ADR-02). 역할 레벨에 박아 psql·pgAdmin 등
-- 앱을 거치지 않는 접속에서도 동일하게 적용되게 한다.
ALTER ROLE kbos_app   SET timezone = 'UTC';
ALTER ROLE kbos_owner SET timezone = 'UTC';

-- 런타임 안전장치. §17.2가 SELECT FOR UPDATE·advisory lock을 예고하므로
-- 잠금 대기가 무한정 쌓이지 않게 상한을 둔다 — 매달리는 대신 빨리 실패한다.
ALTER ROLE kbos_app SET statement_timeout                    = '30s';
ALTER ROLE kbos_app SET lock_timeout                         = '5s';
ALTER ROLE kbos_app SET idle_in_transaction_session_timeout   = '60s';

-- 마이그레이션은 오래 걸릴 수 있으므로 statement_timeout을 걸지 않는다.
-- 다만 DDL이 잠금을 무한 대기하면 운영 배포가 멈추므로 lock_timeout은 둔다.
ALTER ROLE kbos_owner SET statement_timeout = 0;
ALTER ROLE kbos_owner SET lock_timeout      = '10s';
