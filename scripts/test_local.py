"""Local test orchestrator — single command for the whole local stack.

Tiers (cheapest first; each tier subsumes the cheaper ones):

  * Default (``--fast``, implicit): backend pytest (no DB), ruff, mypy,
    frontend vitest, eslint, tsc. ~15-30 s. No servers, no docker.

  * ``--e2e``: also runs the two Phase-5 Playwright specs that are
    hermetic via ``page.route`` stubbing (delta.spec.ts +
    pan_zoom_delta.spec.ts). ~30 s extra. Vite dev server boots
    automatically per the playwright config.

  * ``--docker``: also lints both Dockerfiles via
    ``docker buildx --check``. Doesn't actually build (too slow);
    use ``--docker-build`` for that.

  * ``--db``: **destructive** — brings up the docker-compose data
    plane and runs the DB-gated integration tests against it. The
    autouse TRUNCATE fixtures will wipe any existing local
    ingestion state — confirm with ``--yes`` or interactive prompt.

By default the runner continues past failures and prints a final
pass/fail table. Pass ``--fail-fast`` to halt on the first error
(CI-style).

Usage:
  .venv/Scripts/python -m scripts.test_local
  .venv/Scripts/python -m scripts.test_local --e2e
  .venv/Scripts/python -m scripts.test_local --e2e --docker
  .venv/Scripts/python -m scripts.test_local --db --yes      # destructive
  .venv/Scripts/python -m scripts.test_local --all --yes     # everything
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_FRONTEND = _ROOT / "frontend"
_PY = sys.executable


@dataclass
class StepResult:
    name: str
    status: str  # "pass" / "fail" / "skip"
    duration_ms: int
    detail: str = ""

    @property
    def glyph(self) -> str:
        return {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[self.status]


@dataclass
class Runner:
    fail_fast: bool = False
    results: list[StepResult] = field(default_factory=list)

    def step(
        self,
        name: str,
        cmd: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> StepResult:
        self._banner(name)
        started = time.monotonic()
        process_env = {**os.environ, **(env or {})}
        completed = subprocess.run(list(cmd), cwd=str(cwd) if cwd else None, env=process_env)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        status = "pass" if completed.returncode == 0 else "fail"
        detail = "ok" if completed.returncode == 0 else f"exit {completed.returncode}"
        result = StepResult(name=name, status=status, duration_ms=elapsed_ms, detail=detail)
        self.results.append(result)
        print(f"\n  >>> {result.glyph} in {elapsed_ms} ms ({detail})")
        if self.fail_fast and status == "fail":
            self._summary()
            sys.exit(1)
        return result

    def skip(self, name: str, reason: str) -> None:
        self.results.append(StepResult(name=name, status="skip", duration_ms=0, detail=reason))

    def _banner(self, label: str) -> None:
        bar = "=" * 70
        print(f"\n{bar}\n  {label}\n{bar}")

    def _summary(self) -> None:
        print()
        print("=" * 70)
        print("  SUMMARY")
        print("=" * 70)
        name_width = max((len(r.name) for r in self.results), default=20)
        for r in self.results:
            print(f"  {r.glyph:>4}  {r.name.ljust(name_width)}  {r.duration_ms:>6} ms  {r.detail}")
        passed = sum(1 for r in self.results if r.status == "pass")
        failed = sum(1 for r in self.results if r.status == "fail")
        skipped = sum(1 for r in self.results if r.status == "skip")
        total_ms = sum(r.duration_ms for r in self.results)
        print()
        print(
            f"  total: {len(self.results)}  pass: {passed}  fail: {failed}  skip: {skipped}  ({total_ms} ms)"
        )

    def finish(self) -> int:
        self._summary()
        return 1 if any(r.status == "fail" for r in self.results) else 0


# ---------------------------------------------------------------------------
# Step groups
# ---------------------------------------------------------------------------


def _ensure_frontend_deps(runner: Runner) -> None:
    """Install pnpm deps once if node_modules is missing."""
    if (_FRONTEND / "node_modules").is_dir():
        return
    runner.step("frontend: pnpm install (first run)", ["pnpm", "install"], cwd=_FRONTEND)


def fast_tier(runner: Runner) -> None:
    runner.step(
        "backend: pytest (fast — api/, scripts/, observability)",
        [_PY, "-m", "pytest", "api", "scripts", "tests/test_observability_invariants.py", "-q"],
    )
    runner.step(
        "backend: ruff check",
        [_PY, "-m", "ruff", "check", "api", "scoring", "ingestion", "scripts", "tests"],
    )
    runner.step(
        "backend: ruff format --check",
        [_PY, "-m", "ruff", "format", "--check", "."],
    )
    runner.step(
        "backend: mypy api",
        [_PY, "-m", "mypy", "api"],
    )
    _ensure_frontend_deps(runner)
    runner.step("frontend: vitest", ["pnpm", "test"], cwd=_FRONTEND)
    runner.step("frontend: eslint", ["pnpm", "lint"], cwd=_FRONTEND)
    runner.step("frontend: tsc --noEmit", ["pnpm", "typecheck"], cwd=_FRONTEND)


def e2e_tier(runner: Runner) -> None:
    _ensure_frontend_deps(runner)
    runner.step(
        "playwright: delta-view E2E (hermetic)",
        ["pnpm", "test:e2e", "--", "delta.spec.ts"],
        cwd=_FRONTEND,
    )
    runner.step(
        "playwright: pan/zoom delta benchmark",
        ["pnpm", "bench:frontend", "--", "pan_zoom_delta.spec.ts"],
        cwd=_FRONTEND,
    )


def docker_tier(runner: Runner, *, build: bool) -> None:
    if not _have_docker():
        runner.skip("docker: dockerfile lint", "docker CLI not available")
        return
    runner.step(
        "docker: buildx --check api/Dockerfile",
        ["docker", "buildx", "build", "--file", "api/Dockerfile", "--check", "."],
    )
    runner.step(
        "docker: buildx --check frontend/Dockerfile",
        ["docker", "buildx", "build", "--file", "frontend/Dockerfile", "--check", "."],
    )
    if build:
        runner.step(
            "docker: full build api/Dockerfile",
            ["docker", "build", "-f", "api/Dockerfile", "-t", "streetsense-api:test", "."],
        )


def db_tier(runner: Runner) -> None:
    if not _have_docker():
        runner.skip("db: docker compose up", "docker CLI not available")
        return
    runner.step("db: docker compose up -d", ["docker", "compose", "up", "-d"])
    # Wait briefly for Postgres health — the compose healthcheck retries
    # for 100 s, which is enough for a cold boot.
    runner.step(
        "db: alembic upgrade head",
        [_PY, "-m", "alembic", "upgrade", "head"],
        env={"DATABASE_URL": _local_database_url()},
    )
    runner.step(
        "db: backend integration tests (api/ + tests/api/)",
        [_PY, "-m", "pytest", "api", "tests/api", "tests/test_observability_invariants.py", "-q"],
        env={"DATABASE_URL": _local_database_url()},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _have_docker() -> bool:
    try:
        subprocess.run(
            ["docker", "version"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except FileNotFoundError:
        return False


def _local_database_url() -> str:
    """Match docker-compose.yml's postgres credentials."""
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://streetsense:streetsense@localhost:5432/streetsense",
    )


def _confirm_destructive() -> bool:
    print()
    print("!" * 70)
    print("  --db is DESTRUCTIVE — autouse TRUNCATE fixtures wipe live")
    print("  ingestion state in the local Postgres. Continue?")
    print("!" * 70)
    response = input("  type 'yes' to proceed: ").strip().lower()
    return response == "yes"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _select_tiers(args: argparse.Namespace) -> Iterator[str]:
    """Yield tier names in execution order. ``--all`` expands to everything."""
    if args.all:
        yield from ("fast", "e2e", "docker", "db")
        return
    yield "fast"
    if args.e2e:
        yield "e2e"
    if args.docker or args.docker_build:
        yield "docker"
    if args.db:
        yield "db"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="streetsense-test-local", description=__doc__)
    parser.add_argument("--e2e", action="store_true", help="add the hermetic Playwright tier")
    parser.add_argument(
        "--docker",
        action="store_true",
        help="add Dockerfile lint (cheap; no build)",
    )
    parser.add_argument(
        "--docker-build",
        action="store_true",
        help="add a full docker build (slow — pulls Boost, compiles C++)",
    )
    parser.add_argument(
        "--db",
        action="store_true",
        help="add DB integration tests (DESTRUCTIVE — wipes local ingestion)",
    )
    parser.add_argument("--all", action="store_true", help="run every tier")
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="halt on the first failure (CI mode)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip confirmation for --db (CI use)",
    )
    args = parser.parse_args(argv)

    tiers = list(_select_tiers(args))
    if "db" in tiers and not args.yes and not _confirm_destructive():
        print("aborted by user")
        return 2

    runner = Runner(fail_fast=args.fail_fast)
    print(f"\nstreetsense test_local — tiers: {', '.join(tiers)}")

    if "fast" in tiers:
        fast_tier(runner)
    if "e2e" in tiers:
        e2e_tier(runner)
    if "docker" in tiers:
        docker_tier(runner, build=args.docker_build)
    if "db" in tiers:
        db_tier(runner)

    return runner.finish()


if __name__ == "__main__":
    sys.exit(main())
