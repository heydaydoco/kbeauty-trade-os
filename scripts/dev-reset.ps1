# 개발(dev) DB를 완전히 초기화한다. 운영과 무관하다.
# 이 스크립트는 kbos-dev 프로젝트만 대상으로 한다(prod을 겨냥할 수 없다).

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host ""
Write-Host "  개발 DB의 모든 데이터가 삭제됩니다 (운영과 무관)." -ForegroundColor Yellow
Write-Host "  계속하려면 RESET 을 그대로 입력하세요." -ForegroundColor Yellow
$answer = Read-Host "  입력"

if ($answer -ne "RESET") {
    Write-Host "  취소되었습니다." -ForegroundColor Green
    exit 0
}

Write-Host "  컨테이너와 볼륨을 제거합니다..." -ForegroundColor Cyan
docker compose down -v

Write-Host "  다시 기동합니다..." -ForegroundColor Cyan
docker compose up -d --build

Write-Host ""
Write-Host "  완료. 상태 확인:  docker compose ps" -ForegroundColor Green
