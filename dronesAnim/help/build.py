"""Build the offline manual with the pinned documentation dependencies."""

from pathlib import Path
import subprocess
import sys


if __name__ == "__main__":
    config = Path(__file__).resolve().with_name("mkdocs.yml")
    subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--config-file", str(config)],
        check=True,
    )
    # HTTP error pages are unused in the file-based manual.
    (config.parent / "site" / "404.html").unlink(missing_ok=True)
