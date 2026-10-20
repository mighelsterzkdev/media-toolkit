HEIF decoder notices and corresponding sources

Media Toolkit uses pi-heif 1.4.0 (BSD-3-Clause) to read HEIC/HEIF files.
The official Windows wheel links dynamically to libheif 1.23.0 and
libde265 1.1.1 (LGPL-3.0). No x265 encoder is distributed.
The wheel's upstream LICENSES_bundled.txt is preserved verbatim; its old
version links do not override the actual versions listed here and in SOURCES.json.

The sources/ directory in the portable release contains the unmodified,
checksum-verified source archives for pi-heif, libheif and libde265.
SOURCES.json records their upstream download URLs and SHA-256 checksums.
The pi-heif source includes its Windows build recipe at
libheif/windows/mingw-w64-libheif/PKGBUILD. It builds a shared libheif with
libde265 enabled and X265, AV1 encoders and plugin loading disabled.
The Python wrapper and decoder sources can be modified and rebuilt with
the instructions and build files in those source archives.

The LGPL libraries are independent shared DLLs in _desktop/ and _internal/.
Users may modify these libraries and reverse engineer their use for debugging
such modifications. An interface-compatible rebuilt DLL can replace its
corresponding DLL (using the original DLL filename) beside the application.
The application imposes no signature check on these libraries. Rebuild both
library directories when using both the GUI and terminal executables.
The complete application source and reproducible build scripts are available at
https://github.com/mighelsterzkdev/media-toolkit

The pi-heif wheel also distributes MinGW GCC runtime libraries libgcc_s_seh-1
and libstdc++-6. Compiler identifiers in the wheel report GCC 16.1.0, MSYS2 Rev5.
These runtimes are covered by GPL-3.0 with GCC Runtime Library Exception 3.1;
the full license and exception texts accompany this notice. Upstream sources:
https://ftp.gnu.org/gnu/gcc/gcc-16.1.0/gcc-16.1.0.tar.xz
https://github.com/msys2/MINGW-packages/tree/master/mingw-w64-gcc

The dynamically linked libwinpthread-1 comes from mingw-w64 winpthreads.
Its complete MIT and BSD notices are in WINPTHREADS-COPYING.txt.
Source and build instructions:
https://github.com/mingw-w64/mingw-w64/tree/master/mingw-w64-libraries/winpthreads
https://github.com/msys2/MINGW-packages/tree/master/mingw-w64-winpthreads

All upstream notices are provided without alteration. These libraries are
provided without warranty under their respective license terms.
