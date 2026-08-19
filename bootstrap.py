from __future__ import annotations

from pathlib import Path


# The runtime project still invokes `python bootstrap.py` in its Vercel Build
# Command. Older deployments used this hook to copy a temporary source bundle.
# The repository is now the source of truth, so the hook is intentionally a
# validation-only no-op: it must never overwrite checked-out GitHub files.
# This file is also an explicit deployment trigger for the public runtime.
required = [
    Path("api/main.py"),
    Path("backend/main.py"),
    Path("miniapp/package.json"),
]
missing = [str(path) for path in required if not path.is_file()]
if missing:
    raise SystemExit(f"AlphaPulse repository checkout is incomplete: {', '.join(missing)}")

print("AlphaPulse repository source ready")
