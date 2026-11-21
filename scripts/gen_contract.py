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

EDGE_COPY = "edge/agent/contract.py"
SOURCE = "app/contract/messages.py"


def outputs() -> dict[str, str]:
    """Every generated artifact: the TS zod module and the agent's vendored copy."""
    return {
        OUTPUT_PATH: render(),
        EDGE_COPY: (ROOT / SOURCE).read_text(encoding="utf-8"),
        "edge/agent/speech_matching.py": (ROOT / "app/contract/speech_matching.py").read_text(
            encoding="utf-8"
        ),
    }


def main(argv: list[str]) -> int:
    stale = []
    for rel, fresh in outputs().items():
        target = ROOT / rel
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if "--check" in argv:
            if current != fresh:
                stale.append(rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(fresh, encoding="utf-8")
        print(f"wrote {rel}")
    if stale:
        print(f"stale: {', '.join(stale)}: run `make contract`", file=sys.stderr)
        return 1
    if "--check" in argv:
        print("contract artifacts are up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
