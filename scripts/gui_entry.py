"""Windowed entry point; freeze support must run before desktop initialization."""

import multiprocessing
import sys


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if sys.argv[1:2] == ["--finish-update"]:
        # Run from a staged release after the previous copy exits; installs it, then reopens the app.
        from universal_downloader.updates import finish
        raise SystemExit(finish(sys.argv[2:], "media-toolkit.exe"))
    from universal_downloader.gui import main
    raise SystemExit(main())
