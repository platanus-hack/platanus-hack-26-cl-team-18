"""Write web/src/contract/messages.gen.ts from app/contract/messages.py.

Usage: `make contract` (or `uv run python scripts/gen_contract.py --check`
to fail without writing when the committed file is stale).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.contract.codegen import OUTPUT_PATH, render  # noqa: E402


def main(argv: list[str]) -> int:
    target = ROOT / OUTPUT_PATH
    fresh = render()
    if "--check" in argv:
        if not target.exists() or target.read_text(encoding="utf-8") != fresh:
            print(f"{OUTPUT_PATH} is stale: run `make contract`", file=sys.stderr)
            return 1
        print(f"{OUTPUT_PATH} is up to date")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(fresh, encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
