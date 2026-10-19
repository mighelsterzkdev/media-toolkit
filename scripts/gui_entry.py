"""Windowed entry point; freeze support must run before desktop initialization."""

import multiprocessing


if __name__ == "__main__":
    multiprocessing.freeze_support()
    from universal_downloader.gui import main
    raise SystemExit(main())
