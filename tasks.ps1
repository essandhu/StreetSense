# StreetSense PowerShell task runner.
#
# Windows convenience wrapper around the same commands the Makefile dispatches
# to. Use this if `make` is not on PATH.
#
# Usage:
#   .\tasks.ps1 <target> [-City cambridge]
#
# Example:
#   .\tasks.ps1 seed
#   .\tasks.ps1 test
#   .\tasks.ps1 lint

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Target = "help",
    [string]$City = "cambridge"
)

$ErrorActionPreference = "Stop"

function Invoke-Help {
    Write-Output @"
Targets:
  .\tasks.ps1 help                          Print this list
  .\tasks.ps1 seed -City <slug>             Ingest city OSM into Postgres (default: cambridge)
  .\tasks.ps1 ingest-imagery -City <slug>   Ingest street-level imagery (Phase 3)
  .\tasks.ps1 api                           Start FastAPI service
  .\tasks.ps1 scoring-run                   Trigger a scoring run (Phase 1: stub)
  .\tasks.ps1 test                          Run Python + frontend unit tests
  .\tasks.ps1 lint                          Lint/format-check/typecheck everything
  .\tasks.ps1 db-up                         Start data plane (Postgres + PostGIS + MinIO)
  .\tasks.ps1 db-down                       Stop the data plane
  .\tasks.ps1 migrate                       Apply Alembic migrations
  .\tasks.ps1 clean                         Remove caches and build artifacts
"@
}

function Invoke-DbUp {
    docker compose up -d
}

function Invoke-DbDown {
    docker compose down
}

function Invoke-Migrate {
    uv run alembic upgrade head
}

function Invoke-Seed {
    Invoke-DbUp
    Invoke-Migrate
    uv run python -m ingestion.cli seed --city $City
}

function Invoke-IngestImagery {
    Invoke-DbUp
    Invoke-Migrate
    uv run python -m ingestion.cli imagery --city $City
}

function Invoke-Api {
    Invoke-DbUp
    Invoke-Migrate
    uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
}

function Invoke-ScoringRun {
    Invoke-DbUp
    Invoke-Migrate
    uv run python -m scoring.cli run --city $City
}

function Invoke-TestPy { uv run pytest }
function Invoke-TestFe { Push-Location frontend; try { pnpm test } finally { Pop-Location } }
function Invoke-Test   { Invoke-TestPy; Invoke-TestFe }

function Invoke-LintPy {
    uv run ruff check .
    if ($LASTEXITCODE -ne 0) { throw "ruff check failed" }
    uv run ruff format --check .
    if ($LASTEXITCODE -ne 0) { throw "ruff format check failed" }
    uv run mypy
    if ($LASTEXITCODE -ne 0) { throw "mypy failed" }
}
function Invoke-LintFe {
    Push-Location frontend
    try {
        pnpm lint;          if ($LASTEXITCODE -ne 0) { throw "eslint failed" }
        pnpm format:check;  if ($LASTEXITCODE -ne 0) { throw "prettier failed" }
        pnpm typecheck;     if ($LASTEXITCODE -ne 0) { throw "tsc failed" }
    } finally { Pop-Location }
}
function Invoke-Lint { Invoke-LintPy; Invoke-LintFe }

function Invoke-Clean {
    foreach ($p in @(".pytest_cache", ".mypy_cache", ".ruff_cache", "htmlcov", ".coverage", "frontend/dist", "frontend/coverage")) {
        if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }
    Get-ChildItem -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -Recurse -Force $_.FullName }
}

switch ($Target) {
    "help"            { Invoke-Help }
    "seed"            { Invoke-Seed }
    "ingest-imagery"  { Invoke-IngestImagery }
    "api"             { Invoke-Api }
    "scoring-run" { Invoke-ScoringRun }
    "test"        { Invoke-Test }
    "test-py"     { Invoke-TestPy }
    "test-fe"     { Invoke-TestFe }
    "lint"        { Invoke-Lint }
    "lint-py"     { Invoke-LintPy }
    "lint-fe"     { Invoke-LintFe }
    "db-up"       { Invoke-DbUp }
    "db-down"     { Invoke-DbDown }
    "migrate"     { Invoke-Migrate }
    "clean"       { Invoke-Clean }
    default       { Write-Error "Unknown target: $Target. Run '.\tasks.ps1 help' for the list."; exit 2 }
}
