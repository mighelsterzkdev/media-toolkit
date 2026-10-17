# Universal Downloader

Download a video or extract its audio as MP3 from a URL, using a simple terminal menu.

**Python · yt-dlp · FFmpeg · Windows / Linux / macOS**

```text
UNIVERSAL DOWNLOADER
Video and MP3 downloads from a URL.

Paste the URL (Enter to exit): https://example.com/video

1. Video
2. MP3 audio
Format [1]: 2

Checking the URL...
Title: My video
Downloading: 75.0% | 2.1 MiB/s | 3s remaining

Done! File saved to:
.../downloads/My video [identifier].mp3
```

## Features

- Download one video per URL, including audio when available.
- Extract MP3 audio at 192 kbps. Final audio quality depends on the source.
- Choose the best available resolution or a maximum of 1080p, 720p, or 480p, without upscaling.
- View download progress, speed, estimated time remaining, and the final file path.
- Automatically retry network failures and resume partial downloads when the server supports it.
- Organize files by title and identifier without overwriting completed downloads.
- Check dependencies and update the download engine.

## Quick start on Windows

1. Install [Python 3.10 or later](https://www.python.org/downloads/) and select **Add Python to PATH**.
2. Open **`install.cmd`** and wait for setup to finish.
3. Open **`start.cmd`**, paste a URL, and choose video or MP3.

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

# Limit the resolution and choose an output directory
.\start.cmd "https://example.com/video" --quality 720 --output "D:\Videos"

# Check the installation
.\start.cmd --doctor

# Update yt-dlp
.\update.cmd
```

Replace the example URLs with real video URLs. Quote URLs, especially when they contain `&`. A video that already includes audio keeps its original container. Separate video and audio tracks are merged into MP4 or MKV depending on their codecs. The app does not convert every video to MP4.

## Manual installation: Linux, macOS, and other Windows setups

Install Python 3.10+, the `ffmpeg` and `ffprobe` executables ([FFmpeg](https://ffmpeg.org/download.html)), and either [Deno 2.3+](https://docs.deno.com/runtime/getting_started/installation/) or [Node.js 22+](https://nodejs.org/). Make sure the executables are on PATH.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m universal_downloader --doctor
.venv/bin/python -m universal_downloader
.venv/bin/python -m universal_downloader "https://example.com/video" --format mp3
```

On Windows, replace `.venv/bin/python` with `.venv\Scripts\python.exe` and use `python` instead of `python3` if needed. When the virtual environment is activated, the shorter `unidown` command is also available. When running manually, `downloads` is relative to the terminal's current directory.

## Compatibility and maintenance

The project uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) extractors. See the [supported sites list](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md); being listed does not guarantee that every video on a site is accessible. Direct media URLs recognized by the generic extractor are also supported.

Website changes can affect compatibility. If a URL stops working, run `update.cmd`. To try more recent fixes, use:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade --pre "yt-dlp[default]"
```

On Linux/macOS, use `.venv/bin/python -m pip install --upgrade "yt-dlp[default]"`. Add `--verbose` when troubleshooting a failure.

This first version handles **one video at a time** and does not include login, cookies, playlists, or live streams. Private, removed, DRM-protected, region-restricted, or blocked content may be unavailable. Download content you own or have permission to download.

## Development

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

On Linux/macOS, use `.venv/bin/python -m unittest discover -s tests -v`. Tests cover input validation, menu interactions, and failure handling. Integration tests generate a small media file with FFmpeg and serve it over local HTTP to verify complete video and MP3 downloads without depending on external websites. Integration tests are skipped when FFmpeg/ffprobe are unavailable.

```text
src/universal_downloader/
  cli.py             Terminal menu and arguments
  downloader.py      Download and post-processing logic
  dependencies.py    FFmpeg and JavaScript runtime discovery
scripts/
  install_tools.py   Portable tools for Windows
tests/               Unit tests and local integration tests
```

## Roadmap

- [ ] Download queues and playlists.
- [ ] Download history.
- [ ] Per-video format selection.
- [ ] A graphical interface using the same download module.

## License

The project code is available under the [MIT license](LICENSE). Dependencies have their own licenses; see [yt-dlp](https://github.com/yt-dlp/yt-dlp#licensing), [FFmpeg](https://ffmpeg.org/legal.html), and [Deno](https://github.com/denoland/deno/blob/main/LICENSE.md).
