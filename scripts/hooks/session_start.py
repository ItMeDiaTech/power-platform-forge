"""SessionStart hook: record where the plugin's data directory is, for commands that cannot see it.

Commands Claude runs through the Bash or PowerShell tool do not get CLAUDE_PLUGIN_DATA, so ppx finds
the data directory through the pointer file written here, and Bash commands also through
PPX_DATA_DIR. SessionStart cannot block anything, so a failure prints one line to stderr and exits 0.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ppx"))


def main() -> int:
    try:
        from ppx import datadir

        data = os.environ.get("CLAUDE_PLUGIN_DATA")
        if not data:
            raise RuntimeError("CLAUDE_PLUGIN_DATA is not set")
        Path(data).mkdir(parents=True, exist_ok=True)
        datadir.write_pointer(data, os.environ.get("CLAUDE_PLUGIN_ROOT"))
        env_file = os.environ.get("CLAUDE_ENV_FILE")
        if env_file:
            value = str(Path(data)).replace("\\", "/").replace("'", "'\\''")
            with open(env_file, "a", encoding="utf-8") as f:
                f.write(f"export PPX_DATA_DIR='{value}'\n")
    except Exception as exc:
        print(f"power-platform-forge: could not record the data directory: {' '.join(str(exc).split())}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
