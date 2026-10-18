# Universal Downloader

Download videos, audio, images, and video thumbnails from a URL, using a simple terminal menu.

**Python · yt-dlp · FFmpeg · Pillow · Windows / Linux / macOS**

```text
UNIVERSAL DOWNLOADER
Video, audio, images, and thumbnails from a URL.

Paste the URL (Enter to exit): https://example.com/video

1. Video
2. Audio
3. Image / thumbnail
4. Available source formats
Type [1]: 3

Image format
1. JPG / JPEG
2. PNG
3. WebP
4. AVIF
5. GIF (static)
6. BMP
7. TIFF
8. ICO (Windows icon)
9. Original image / thumbnail
Format [1]: 3

Checking the URL...
Title: My video

Done! File saved to:
.../downloads/My video [identifier] [image].webp
```

## Features

- Download one video, audio track, image, or thumbnail per URL.
- Choose among 27 output options, including original video, original audio, and original images.
- Save a video's best reported thumbnail without downloading the video. If a thumbnail fails, try the next available one.
- Download direct image URLs and images exposed through Open Graph, Twitter image tags, or a video poster.
- Choose a video resolution limit up to 4K, 1440p, 1080p, 720p, 480p, or 360p, when the source reports those resolutions.
- Adjust lossy audio bitrate from 64 to 320 kbps and JPG/WebP/AVIF quality from 1 to 95.
- List the source formats offered by a URL and optionally select an exact format ID or video+audio pair.
- View download progress, speed, estimated time remaining, and the final file path.
- Automatically retry network failures and resume partial downloads when the server supports it.
- Organize files by title and identifier without overwriting completed downloads.
- Check dependencies and update the download and image engines.

## Output formats

| Type | Choices |
| --- | --- |
| Video | Original/automatic (`video`), **MP4, MKV, WebM, MOV, AVI, M4V, FLV, TS** |
| Audio | **MP3, M4A/AAC, AAC, Opus, OGG/Vorbis, FLAC, WAV, ALAC**, original audio (`audio`) |
| Image / thumbnail | **JPG/JPEG, PNG, WebP, AVIF, GIF, BMP, TIFF, ICO**, original image (`image`) |

Automatic video keeps the source codecs and container when possible. Separate video/audio streams are merged into MP4 or MKV. Choosing an explicit video format re-encodes it with FFmpeg, which can take time for large files. MP4/MKV/MOV/M4V/TS use H.264 + AAC, WebM uses VP9 + Opus, AVI uses Xvid + MP3, and FLV uses FLV1 + MP3. Silent videos remain silent.

Original audio copies the source audio when possible. `alac` produces a **`.m4a`** file using the ALAC codec; `aac` produces a raw **`.aac`** file. FLAC, WAV, and ALAC preserve the decoded source quality; they cannot restore detail lost in the original. The default target bitrate for lossy audio is **192 kbps**.

Image conversion uses the actual image encoder rather than renaming a file. PNG/WebP/AVIF/TIFF preserve transparency where supported. JPG/BMP flatten transparent areas onto white. ICO fits the image into a square up to 256×256 pixels. GIF conversion produces a static image. To keep an animated image intact, use **original image** (`--format image`); converting an animated input is rejected with a clear message.

## Quick start on Windows

1. Install [Python 3.10 or later](https://www.python.org/downloads/) and select **Add Python to PATH**.
2. Open **`install.cmd`** and wait for setup to finish.
3. Open **`start.cmd`**, paste a URL, and choose **Video**, **Audio**, or **Image / thumbnail**.

Existing installations should run `install.cmd` again after updating the project to install the new Pillow dependency. `update.cmd` updates both yt-dlp and Pillow. Dependencies are already installed when setup reports success.

Files are saved to the **`downloads`** directory inside the project. The automatic installer supports Windows x64, creates a `.venv`, installs FFmpeg/ffprobe into `.tools` when needed, and prepares a local copy of Deno for YouTube support. These executables are excluded from Git and are not added to your system PATH. Downloaded installer assets are verified against the SHA-256 checksums published in their GitHub releases.

To use a Python executable at a specific path:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Python 'C:\path\to\python.exe'
```

## Command-line usage

On Windows, run these commands from the project directory:

```powershell
# Open the interactive menu
.\start.cmd

# Download video at the best available quality
.\start.cmd "https://example.com/video"

# Extract MP3 audio
.\start.cmd "https://example.com/video" --format mp3

# Download and encode a video as WebM
.\start.cmd "https://example.com/video" --format webm

# Extract Opus audio at a target bitrate of 128 kbps
.\start.cmd "https://example.com/video" --format opus --audio-bitrate 128

# Extract FLAC or ALAC audio
.\start.cmd "https://example.com/video" --format flac
.\start.cmd "https://example.com/video" --format alac

# Save only the thumbnail, without downloading the video
.\start.cmd "https://example.com/video" --format webp --image-quality 85
.\start.cmd "https://example.com/video" --format avif

# Keep the original thumbnail format and bytes
.\start.cmd "https://example.com/video" --format image

# Download a direct image URL as TIFF
.\start.cmd "https://example.com/picture.png" --format tiff

# Limit the resolution and choose an output directory
.\start.cmd "https://example.com/video" --quality 720 --output "D:\Videos"

# Inspect available source format IDs without saving files
.\start.cmd "https://example.com/video" --list-formats

# Use IDs from the list (the IDs below are only examples)
.\start.cmd "https://example.com/video" --source-format "137+140"

# Check the installation
.\start.cmd --doctor

# Update the download and image engines
.\update.cmd
```

Replace the example URLs with real URLs. Quote URLs, especially when they contain `&`. Format names are case-insensitive; `jpeg`, `tif`, `vorbis`, and `thumbnail` are accepted aliases for `jpg`, `tiff`, `ogg`, and `image`. `--quality` is a video source selection limit, not an upscaler. `--source-format` uses the selector syntax of [yt-dlp](https://github.com/yt-dlp/yt-dlp#format-selection) and cannot be combined with `--quality`.

Existing completed files are kept. Format, audio bitrate, resolution, and custom source selector tags distinguish media outputs. Image outputs use an `[image]` tag so they can coexist with the video. To produce another image quality setting for the same URL and format, choose another output folder.

## Manual installation: Linux, macOS, and other Windows setups

Install Python 3.10+, the `ffmpeg` and `ffprobe` executables ([FFmpeg](https://ffmpeg.org/download.html)), and either [Deno 2.3+](https://docs.deno.com/runtime/getting_started/installation/) or [Node.js 22+](https://nodejs.org/). Make sure the executables are on PATH.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m universal_downloader --doctor
.venv/bin/python -m universal_downloader
.venv/bin/python -m universal_downloader "https://example.com/video" --format mp3
.venv/bin/python -m universal_downloader "https://example.com/video" --format png
```

On Windows, replace `.venv/bin/python` with `.venv\Scripts\python.exe` and use `python` instead of `python3` if needed. When the virtual environment is activated, the shorter `unidown` command is also available. When running manually, `downloads` is relative to the terminal's current directory.

## Compatibility and maintenance

The project uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) extractors. See the [supported sites list](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md); being listed does not guarantee that every video on a site is accessible. Direct media URLs recognized by the generic extractor are also supported.

Website changes can affect compatibility. If a URL stops working, run `update.cmd`. To try more recent fixes, use:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade --pre "yt-dlp[default]"
```

On Linux/macOS, use `.venv/bin/python -m pip install --upgrade "yt-dlp[default]" "Pillow>=12.0,<13"`. Add `--verbose` when troubleshooting a failure.

The app handles **one item at a time** and does not include login, cookies, playlists, or live media downloads. Private, removed, DRM-protected, region-restricted, or blocked content may be unavailable. Download content you own or have permission to download.

A thumbnail must be exposed by the source page; not every URL has one. A direct MP4 link often has no associated thumbnail, so use the video's webpage URL instead. Images can be saved without FFmpeg; extraction from some sites still needs a JavaScript runtime. Direct images are limited to **32 MB** and **40 million pixels**. Output encoders depend on the installed Pillow/FFmpeg builds; `--doctor` reports the available image encoders.

## Development

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

On Linux/macOS, use `.venv/bin/python -B -m unittest discover -s tests -v`. Tests cover input validation, menu interactions, and failure handling. Local integration tests decode every advertised audio/video format with ffprobe and every advertised image format with Pillow. They also verify source format selection, thumbnail fallback, animation handling, bitrate, preservation of completed files, and image-only requests that never fetch the video. Test media lives in temporary directories and is cleaned up automatically. Integration tests are skipped when FFmpeg/ffprobe are unavailable; CI installs them on both Windows and Linux.

```text
src/universal_downloader/
  cli.py             Terminal menu and arguments
  downloader.py      Download and post-processing logic
  media.py           Audio/video encoding profiles
  images.py          Thumbnail selection and image encoding
  formats.py         Shared output format catalog
  errors.py          User-facing error type
  dependencies.py    FFmpeg and JavaScript runtime discovery
scripts/
  install_tools.py   Portable tools for Windows
tests/               Unit tests and local integration tests
```

## Local files and cleanup

The Windows launcher and installation checks use Python's `-B` option to avoid creating bytecode caches in the source directories. The test command above does the same.

- `__pycache__/` and `*.pyc` are disposable Python caches, excluded from Git.
- `downloads/` contains downloaded media. Remove files there only when you no longer want them. No `verification/` folder is required by the app.
- `.venv/` contains the installed Python environment; `.tools/` contains FFmpeg, ffprobe, and Deno. Keep them to run the app without reinstalling.
- `*.egg-info/` is generated package metadata. Keep it with the development installation; it is excluded from Git.
- `tests/` and `.github/workflows/` contain the automated checks, not leftover download samples.

## Roadmap

- [ ] Download queues and playlists.
- [ ] Download history.
- [x] Per-video source format selection.
- [x] Image and thumbnail downloads.
- [x] Additional audio, video, and image output formats.

## License

The project code is available under the [MIT license](LICENSE). Dependencies have their own licenses; see [yt-dlp](https://github.com/yt-dlp/yt-dlp#licensing), [FFmpeg](https://ffmpeg.org/legal.html), [Pillow](https://github.com/python-pillow/Pillow/blob/main/LICENSE), and [Deno](https://github.com/denoland/deno/blob/main/LICENSE.md).
