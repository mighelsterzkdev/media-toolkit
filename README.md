# Universal Downloader

Paste a link, check the available content, and choose **Original (recommended)** or a compatible format before downloading.

**Python · yt-dlp · FFmpeg · Pillow · Windows / Linux / macOS**

```text
UNIVERSAL DOWNLOADER
Check a link, then choose the original or another format.

Paste the URL (Enter to exit): https://example.com/video

Checking the URL...

Title: My video
Duration: 2:30
Available: Video, Audio, Thumbnail

1. Video
2. Audio
3. Thumbnail
0. Another URL
Download [1]: 3

1. Original (recommended)
2. PNG image
3. JPG image
0. Another URL
Save as [1]: 1

Saved:
.../downloads/My video [identifier] [image].jpg

Paste the URL (Enter to exit):
```

## How it works

- Check the link before offering download choices. Only show verified content.
- Choose the content when a link has multiple kinds, then choose **Original (recommended)** or a compatible conversion. Downloading starts only after that choice.
- **Video:** keep the source streams and use the best available quality. Separate video and audio streams are merged without re-encoding.
- **Audio:** download the original audio file when available. If the audio is inside a video, copy its audio stream into a suitable container without re-encoding. An AAC track may become an M4A file; other codecs keep their own compatible container.
- **Image / thumbnail:** save the exact original bytes of the best reachable image, keeping its format, transparency, metadata, and animation.
- The menu names the detected asset: **Image**, **Thumbnail**, or **Animation**. A video's static cover is separate from its primary media.
- **Twitter/X GIFs:** recognize the animated media asset and show **Animation**. If the source delivers the animation as MP4, keep that animated MP4, without downloading its static JPEG preview or converting it to GIF. The image download command handles these animations automatically.
- Even a link containing only one kind of media shows the output menu before downloading. Press Enter to select the recommended original, or **0** to choose another URL.
- Completed files are kept and reported as **Already downloaded**.
- After a download or a failed link, paste another URL in the same session. Enter on an empty URL exits; **0** goes back from the download menu.
- Reuse inspected metadata, cookies created by the source website, and image bytes when downloading.
- Resume partial downloads when supported. Show progress, transfer speed, and time remaining.

Original downloads do not ask for encoding settings. "Original" means the media made available by the website, which may already be compressed by that website.

## Optional conversion

Every link pasted into the interactive session shows **Original (recommended)** as option **1**, followed by compatible conversions. There is no separate conversion mode. For example, an animation offers:

```text
1. Original (recommended)
2. GIF animation
3. MP4 video
0. Another URL
Save as [1]:
```

The available formats depend on the detected content:

| Detected content | Optional formats |
| --- | --- |
| Video | MP4 (H.264 + AAC for compatibility) |
| Audio | MP3 |
| Image or thumbnail | PNG, JPG |
| Animation | GIF, MP4 |

Conversions can change quality; they do not restore lost source detail. Animated content is not offered static-image conversions. Re-encoding an animation as GIF supports up to **30 seconds**, at 15 fps and up to 720 pixels on the longest side. Original downloads and unchanged native GIF files are not restricted by that conversion limit. Images and MP3/GIF files already in the requested format are kept without re-encoding when supported.

Conversion uses the same inspected source and downloads it once into a private work folder. Only the completed result appears in the output folder. Temporary work is cleaned after success, failure, or cancellation, and completed results are reused.

Direct commands are also available:

```powershell
.\start.cmd "https://example.com/video" --convert mp3
.\start.cmd "https://example.com/video" --convert mp4
.\start.cmd "https://example.com/animation" --convert gif
.\start.cmd "https://example.com/photo.webp" --convert png
```

Use `--type` to choose a specific source asset when needed, such as `--type image --convert jpg` for a thumbnail. `jpeg` is accepted as an alias for `jpg`. No conversion happens without the command or menu selection.

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

Quote URLs, especially when they contain `&`. Type names are case-insensitive, and `thumbnail` is an alias for `image`. Existing `--format video|audio|image` commands still work as aliases for `--type`. Converted formats use the separate `--convert` option; manual bitrate and resolution settings are not part of the normal flow.

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

Tests use temporary local HTTP fixtures. They verify original image/video bytes, audio packet hashes before and after extraction, converted codecs and animation frames, unchanged audio-only files, stream merging, temporary-file cleanup, completed-file preservation, thumbnail fallback, metadata and cookie reuse, and repeated downloads in the terminal. CI installs FFmpeg on Windows and Linux.

Source adapters preserve provider metadata before inspection; the Twitter adapter retains the API's declared media type. `inspection.py` describes content and asset roles, `downloader.py` handles original downloads, and `conversions.py` handles optional local transformations. Converters do not extract URLs or choose thumbnails.

The launcher and test commands use `-B` to avoid source bytecode caches. Keep **.venv/** and **.tools/** to run without reinstalling. **tests/** and **.github/workflows/** contain automated checks. Downloaded media, caches, and generated package metadata are excluded from Git.

## License

The project code uses the [MIT license](LICENSE). Dependencies retain their own licenses: [yt-dlp](https://github.com/yt-dlp/yt-dlp#licensing), [FFmpeg](https://ffmpeg.org/legal.html), [Pillow](https://github.com/python-pillow/Pillow/blob/main/LICENSE), and [Deno](https://github.com/denoland/deno/blob/main/LICENSE.md).
