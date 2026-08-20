param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,
        [Parameter(Mandatory = $true)]
        [string]$FailureMessage
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

Push-Location $ProjectRoot
try {
    Write-Host "[1/5] 检查 Compose 配置"
    Invoke-CheckedCommand { docker compose config --quiet } "Compose 配置无效"

    Write-Host "[2/5] 检查容器状态"
    Invoke-CheckedCommand { docker compose ps } "无法读取 Compose 服务状态"

    Write-Host "[3/5] 检查 PostgreSQL 和迁移版本"
    Invoke-CheckedCommand {
        docker compose exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
    } "PostgreSQL 未就绪"
    Invoke-CheckedCommand { docker compose exec -T app alembic current } "无法读取数据库迁移版本"

    Write-Host "[4/5] 检查 Redis"
    $RedisResponse = docker compose exec -T redis redis-cli ping
    if ($LASTEXITCODE -ne 0 -or ($RedisResponse -join "").Trim() -ne "PONG") {
        throw "Redis 未就绪"
    }

    Write-Host "[5/5] 检查 HTTP 健康端点"
    $Health = Invoke-RestMethod -Uri "$BaseUrl/api/health" -TimeoutSec 10
    if (-not $Health.message) {
        throw "健康端点响应缺少应用信息"
    }

    Write-Host "部署基础验收通过。请继续人工验证管理员登录和核心业务流程。"
}
finally {
    Pop-Location
}
