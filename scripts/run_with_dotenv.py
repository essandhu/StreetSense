"""Bootstrap .env into os.environ, then exec a Python module entrypoint.

Mirrors what `uv run` does on machines where uv is on PATH. Use from PowerShell:
    .\\.venv\\Scripts\\python.exe scripts\\run_with_dotenv.py -m ingestion.cli imagery --city cambridge
"""

from __future__ import annotations

import runpy
import sys

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    if len(sys.argv) < 3 or sys.argv[1] != "-m":
        print("usage: run_with_dotenv.py -m <module> [args...]", file=sys.stderr)
        return 2
    module = sys.argv[2]
    sys.argv = [module, *sys.argv[3:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
