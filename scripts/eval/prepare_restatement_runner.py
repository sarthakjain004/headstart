"""Reserve scratch space for full-prefix replay on disposable GitHub Linux runners."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

MIN_FREE = 40 * 1024**3
UNUSED_SDKS = (
    "/usr/local/lib/android/sdk",
    "/usr/share/dotnet",
    "/usr/local/.ghcup",
)


def available():
    roots = (Path.cwd(), Path(tempfile.gettempdir()))
    free = [shutil.disk_usage(root).free for root in roots]
    for root, size in zip(roots, free, strict=True):
        print(f"Replay scratch: {root}; {size:,} bytes free", flush=True)
    return min(free)


def main():
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("RUNNER_OS") != "Linux"
    ):
        raise RuntimeError(
            "SDK cleanup is restricted to disposable GitHub Linux runners"
        )
    for directory in UNUSED_SDKS:
        if available() >= MIN_FREE:
            return
        target = Path(directory)
        if target.is_dir() and not target.is_symlink():
            subprocess.run(["du", "-sh", directory], check=True)
            subprocess.run(["sudo", "rm", "-rf", "--", directory], check=True)
            print(
                f"Removed unused runner SDK: {directory}; restored on next runner",
                flush=True,
            )
    if available() < MIN_FREE:
        raise RuntimeError(
            "Full-prefix replay needs at least 40 GiB free before input fetch"
        )


if __name__ == "__main__":
    main()
