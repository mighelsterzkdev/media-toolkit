"""Python-free Windows entry point; external tools are fetched from their publishers."""

import sys

from scripts.install_tools import main as install_tools
from universal_downloader.compatibility import compatibility_issues
from universal_downloader.dependencies import find_ffmpeg, find_js_runtime
from universal_downloader.cli import main


def portable_main() -> int:
    issues = compatibility_issues()
    if issues:
        print("This portable release is incomplete. Extract the complete ZIP again.")
        for issue in issues:
            print(f"- {issue}")
        return 1
    if sys.argv[1:] == ["--install-tools"]:
        return install_tools()
    if not any(argument in sys.argv[1:] for argument in ("--help", "-h", "--version", "--doctor")):
        if not find_ffmpeg() or not find_js_runtime():
            print("First start: preparing FFmpeg and the JavaScript runtime from their publishers.")
            print("The pinned downloads are checked with SHA-256 and saved beside this application.\n")
            if install_tools():
                return 1
    return main()


if __name__ == "__main__":
    raise SystemExit(portable_main())
