"""Compatibility launcher for the packaged, checksum-pinned tool installer."""

import sys

from universal_downloader import tool_setup


if __name__ == '__main__':
    raise SystemExit(tool_setup.main())

# Keep existing imports and patch locations working while the implementation is
# also available in an installed wheel launched outside the source checkout.
sys.modules[__name__] = tool_setup
