-- ============================================================================
-- 10-databases.sql — DB 3종 생성 (멱등)
-- 근거: DESIGN.md §18.2(환경 분리), §14(전역 검색), §18.4(인덱스)
--
--   kbos_dev   : 개발 런타임
--   kbos_test  : pytest 전용 (실제 커밋 + TRUNCATE 격리)
--   kbos_migr  : 마이그레이션 왕복 검사 전용 (upgrade→downgrade→upgrade)
--
-- ★ 로케일은 되돌릴 수 없는 결정이다 (ENCODING·LC_CTYPE은 컬럼·쿼리 단위
--   COLLATE로 덮을 수 없고, 바꾸려면 덤프 후 DB 재생성이다).
--   PostgreSQL 16.14에서 실측한 결과:
--
--                          LC_CTYPE 'C'     LC_CTYPE 'C.UTF-8'
--     show_trgm('수출제품')   {} (빈 값)      {트라이그램 5개}
--     similarity              0               0.571
--     '수출' ~ '[[:alpha:]]'  false           true
--     upper('가나dé')         '가나Dé'        '가나DÉ'
--
--   즉 LC_CTYPE 'C'를 택하면 §14 전역 검색의 한국어 부분일치(pg_trgm GIN) 경로가
--   DB 레벨에서 영구히 막힌다. → LC_CTYPE은 'C.UTF-8'.
--
--   반대로 LC_COLLATE는 'C'로 둔다. 한글 음절은 코드포인트 순서가 곧 가나다
--   순서라 정렬 손실이 사실상 없고('가방 < 나무 < 다리 < 마을 < 하늘' 실측 확인),
--   datcollversion이 NULL이 되어 glibc 버전이 바뀌어도 인덱스가 깨지지 않는다.
-- ============================================================================

\set ON_ERROR_STOP on

SELECT format(
         'CREATE DATABASE %I OWNER %I TEMPLATE template0 '
         'ENCODING ''UTF8'' LC_COLLATE ''C'' LC_CTYPE ''C.UTF-8''',
         v.d, 'kbos_owner')
FROM (VALUES ('kbos_dev'), ('kbos_test'), ('kbos_migr')) AS v(d)
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = v.d)
\gexec

-- ── 접속 권한 ────────────────────────────────────────────────────────────
-- PUBLIC(=모든 롤)의 기본 CONNECT를 회수하고 필요한 역할에만 되돌려준다.
REVOKE ALL ON DATABASE kbos_dev  FROM PUBLIC;
REVOKE ALL ON DATABASE kbos_test FROM PUBLIC;
REVOKE ALL ON DATABASE kbos_migr FROM PUBLIC;

GRANT CONNECT, TEMPORARY ON DATABASE kbos_dev  TO kbos_owner, kbos_app;
GRANT CONNECT, TEMPORARY ON DATABASE kbos_test TO kbos_owner, kbos_app;

-- kbos_migr은 마이그레이션 왕복 검사 전용이라 런타임 계정이 붙을 이유가 없다.
GRANT CONNECT, TEMPORARY ON DATABASE kbos_migr TO kbos_owner;
