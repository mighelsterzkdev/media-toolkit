# Universal Downloader

Paste a link, choose **Video**, **Audio**, or **Image / thumbnail**, and download the original media at the best quality available from the source.

**Python · yt-dlp · FFmpeg · Pillow · Windows / Linux / macOS**

```text
UNIVERSAL DOWNLOADER
Original media. Best available quality.

Paste the URL (Enter to exit): https://example.com/video

Checking the URL...

Title: My video
Duration: 2:30
Available: Video, Audio, Image / thumbnail

1. Video
2. Audio
3. Image / thumbnail
0. Another URL
Download [1]: 3

Saved:
.../downloads/My video [identifier] [image].jpg

Paste the URL (Enter to exit):
```

## How it works

- Check the link before offering download choices. Only show verified content.
- Choose what to download; the app handles the format and quality automatically.
- **Video:** keep the source streams and use the best available quality. Separate video and audio streams are merged without re-encoding.
- **Audio:** download the original audio file when available. If the audio is inside a video, copy its audio stream into a suitable container without re-encoding. An AAC track may become an M4A file; other codecs keep their own compatible container.
- **Image / thumbnail:** save the exact original bytes of the best reachable image, keeping its format, transparency, metadata, and animation.
- **Twitter/X GIFs:** recognize the animated media asset and show **Animation**. If the source delivers the animation as MP4, keep that animated MP4, without downloading its static JPEG preview or converting it to GIF. The image download command handles these animations automatically.
- A link containing only one kind of media downloads immediately, without a choice menu.
- Completed files are kept and reported as **Already downloaded**.
- After a download or a failed link, paste another URL in the same session. Enter on an empty URL exits; **0** goes back from the download menu.
- Reuse inspected metadata, cookies created by the source website, and image bytes when downloading.
- Resume partial downloads when supported. Show progress, transfer speed, and time remaining.

There are no conversion, resolution, or bitrate menus. "Original" means the media made available by the website, which may already be compressed by that website.

## Quick start on Windows

1. Install [Python 3.10 or later](https://www.python.org/downloads/) and select **Add Python to PATH**.
2. Open **install.cmd** and wait for setup to finish.
3. Open **start.cmd** and paste a URL.

Downloads are saved inside the project's **downloads/** directory. The installer prepares a **.venv** and portable FFmpeg/ffprobe and Deno inside **.tools**. These tools are excluded from Git and are not added to the system PATH. Installer assets are verified against the SHA-256 checksums published with their releases.

Run **update.cmd** when a supported website stops working. It updates yt-dlp and Pillow.

```powershell
# Open the interactive menu
.\start.cmd

# Download the best available original media automatically
.\start.cmd "https://example.com/video"

# Keep only the original audio
.\start.cmd "https://example.com/video" --type audio

# Save the original thumbnail
.\start.cmd "https://example.com/video" --type image

# Download a direct image without converting it
.\start.cmd "https://example.com/photo.webp"

# Choose a download folder
.\start.cmd "https://example.com/video" --output "D:\Videos"

# Check the installation
.\start.cmd --doctor

# Show technical details when troubleshooting
.\start.cmd "https://example.com/video" --verbose
```

Quote URLs, especially when they contain `&`. Type names are case-insensitive, and `thumbnail` is an alias for `image`. Existing `--format video|audio|image` commands still work as aliases for `--type`; conversion formats and manual encoding options have been removed.

To use a specific Python installation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Python 'C:\path\to\python.exe'
```

## Linux, macOS, and manual installation

Install Python 3.10+, [FFmpeg and ffprobe](https://ffmpeg.org/download.html), and [Deno 2.3+](https://docs.deno.com/runtime/getting_started/installation/) or [Node.js 22+](https://nodejs.org/). Add the executables to PATH.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -B -m universal_downloader --doctor
.venv/bin/python -B -m universal_downloader
.venv/bin/python -B -m universal_downloader "https://example.com/video" --type audio
```

On Windows, use `.venv\Scripts\python.exe`. An activated virtual environment also provides the `unidown` command. With manual launches, the default download folder is relative to the current terminal directory.

## Compatibility

The app uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) for video and audio extraction. Site changes and access restrictions can affect downloads; a successful content check cannot guarantee later access.

One item is downloaded at a time. Playlists, live video/audio, user-account login, and DRM-protected media are not supported. Live videos may still expose a downloadable thumbnail.

Thumbnails must be exposed by the source page. A direct video file often has no thumbnail; use its webpage URL instead. Direct image URLs and Open Graph, Twitter image tags, and video posters are supported. Images are limited to **32 MB** and **40 million pixels** and can be saved without FFmpeg.

## Development

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Tests use temporary local HTTP fixtures. They verify original image/video bytes, audio packet hashes before and after extraction, unchanged audio-only files, stream merging, completed-file preservation, thumbnail fallback, metadata and cookie reuse, and repeated downloads in the terminal. CI installs FFmpeg on Windows and Linux.

The launcher and test commands use `-B` to avoid source bytecode caches. Keep **.venv/** and **.tools/** to run without reinstalling. **tests/** and **.github/workflows/** contain automated checks. Downloaded media, caches, and generated package metadata are excluded from Git.

## License

The project code uses the [MIT license](LICENSE). Dependencies retain their own licenses: [yt-dlp](https://github.com/yt-dlp/yt-dlp#licensing), [FFmpeg](https://ffmpeg.org/legal.html), [Pillow](https://github.com/python-pillow/Pillow/blob/main/LICENSE), and [Deno](https://github.com/denoland/deno/blob/main/LICENSE.md).
