-- ============================================================================
-- 20-grants.sql — 스키마 권한 + 기본권한(ALTER DEFAULT PRIVILEGES) (멱등)
-- 근거: DESIGN.md §17.5, §18.1 / ADR-0002
--
-- ★ 이 파일이 없으면 다음 세션(S0-2)이 중반에 죽는다.
--   역할만 나누고 기본권한을 안 걸면 kbos_owner가 만든 테이블에 kbos_app의
--   권한이 0이라 모든 조회·쓰기가 `permission denied for table ...`로 실패한다.
--   S0-1은 테이블이 0개라 증상이 나타나지 않는다 — 가장 늦게 발견되는 실패다.
--
-- ★ 여기에 `GRANT ... ON ALL TABLES IN SCHEMA public`을 절대 추가하지 마라.
--   이 파일은 매 기동마다 다시 실행된다. ALL TABLES 일괄 부여를 넣으면
--   S0-2가 audit_log에, S4-1이 stock_movements·확정 분개에 건 REVOKE를
--   다음 `docker compose up`이 조용히 되돌린다(§17.5 무력화).
--   신규 테이블 권한은 아래 DEFAULT PRIVILEGES가 생성 시점에 자동으로 붙인다.
-- ============================================================================

\set ON_ERROR_STOP on


-- ─────────────────────────── kbos_dev ───────────────────────────
\connect kbos_dev

REVOKE ALL   ON SCHEMA public FROM PUBLIC;
GRANT  ALL   ON SCHEMA public TO   kbos_owner;   -- 테이블 생성(마이그레이션)
GRANT  USAGE ON SCHEMA public TO   kbos_app;     -- CREATE 없음 → 앱은 DDL 불가

ALTER DEFAULT PRIVILEGES FOR ROLE kbos_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO kbos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE kbos_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO kbos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE kbos_owner IN SCHEMA public
  GRANT EXECUTE ON FUNCTIONS TO kbos_app;


-- ─────────────────────────── kbos_test ──────────────────────────
\connect kbos_test

REVOKE ALL   ON SCHEMA public FROM PUBLIC;
GRANT  ALL   ON SCHEMA public TO   kbos_owner;
GRANT  USAGE ON SCHEMA public TO   kbos_app;

ALTER DEFAULT PRIVILEGES FOR ROLE kbos_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO kbos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE kbos_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO kbos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE kbos_owner IN SCHEMA public
  GRANT EXECUTE ON FUNCTIONS TO kbos_app;


-- ─────────────────────────── kbos_migr ──────────────────────────
-- 마이그레이션 왕복 검사 전용. 런타임 계정에는 아무 권한도 주지 않는다.
\connect kbos_migr

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT  ALL ON SCHEMA public TO   kbos_owner;
