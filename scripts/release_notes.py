"""Print the README section for one version, used as the GitHub release notes."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def notes(version: str) -> str:
    lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(f"## Version {version}") + 1
    except ValueError:
        raise SystemExit(f"README.md has no '## Version {version}' section.")
    end = next((index for index in range(start, len(lines)) if lines[index].startswith("## ")), len(lines))
    return "\n".join(lines[start:end]).strip()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(notes(sys.argv[1]))
