# Media Toolkit

A desktop app with two tools: **Download** saves media from a URL in its original or a compatible format; **Convert** changes local images, audio, video and animations into compatible formats. Both tools share conversion engines and one queue. The original terminal downloader remains available.

**Desktop: Windows 10/11 · React / shadcn/ui / pywebview**
**Download engine and terminal: Python · yt-dlp · FFmpeg · Pillow · Windows / Linux / macOS**

![Media Toolkit desktop interface](docs/media-toolkit.jpg)

## Version 0.13.1

- One spacing, height and type scale across the interface, shared with Clearspace: 40 px main actions and link field, 36 px buttons and format chips, 32 px compact buttons and tabs, 8 px panel corners, 24 px between blocks, 14 px text with 12 px details.

## Version 0.13.0

- **Automatic updates.** Once a day the app checks this repository's releases. A notice offers **Update**; the ZIP is checked against the release's `SHA256SUMS.txt`, and **Restart to update** installs it in the same folder. Downloads, `.tools` and settings are kept, shortcuts keep working, and nothing downloads or restarts without a click. **Settings → Check for updates** checks right away.
- Every photo of an X post is offered, and **Download all** saves them together.
- Ordinary web pages show every picture they publish as a gallery, in original size (images behind Next.js optimizers and CSS backgrounds too), next to any video on the page. Icons and avatars are left out. Filter videos or pictures, mark several, and **Download marked**.
- Format chips for choosing what to save or convert; Portuguese (Brazil) and English interface.
- Drop files on the window to convert them; drop links on the Download tab. Queue items show a cover image.
- Light, dark and system themes with a custom title bar, taskbar progress, a Windows notification when the queue finishes, and an app icon.

## Desktop app

Open **start.cmd** for the dark desktop interface. The project launcher uses the current portable release when present, otherwise the local source environment. It uses bundled React and shadcn/ui components inside Microsoft Edge WebView2; it does not need Node.js or an external website at runtime.

1. In **Download**, paste a URL and click **Check**. Inspection can be cancelled.
2. Select a verified media item, choose **Original** or an available conversion, and click **Download**. A post with several photos or videos (an X post, for example) shows each one as a card; **Download all** queues every item in its original format.
3. Follow progress in the queue. Cancel a running/queued item, retry a failed or cancelled item, or open a completed item's folder.
4. Use **Add multiple links** for several URLs. This explicitly queues each link's recommended original; ambiguous pages report that they need individual selection.
5. Use **Browse** to select the download folder and **Settings** for optional limits. These use the same remembered preferences as the terminal.
6. Drop a link anywhere on the window to check it, or drop files to open them in **Convert**.

The interface is available in Portuguese (Brazil) and English; the **PT/EN** button switches language, which starts matching Windows. While work runs, the taskbar button shows progress, and Windows shows a notification when the queue finishes in the background.

Checks, downloads and conversions run in contained worker processes. Cancelling or closing stops the worker and its child tools without freezing the window. The desktop queue is saved locally and restored when the app reopens. Unfinished tasks wait for an explicit **Retry**; reopening the app does not start downloads or conversions. Supported interrupted transfers reuse verified partial work. Restored selected media is checked again before retry; ambiguous or changed selections require checking the link again instead of choosing another item.

The desktop app is tested on Windows x64 and requires the [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/), normally available on Windows 11. The app uses its own private renderer profile under local application data and does not access your signed-in browser. It loads only the packaged interface and does not open downloaded web pages inside the privileged window.

**start.cmd** with arguments still forwards them to the terminal, so existing scripts continue to work. For the interactive terminal, run `.venv\Scripts\python.exe -B -m universal_downloader` from source or `unidown.exe` from the portable folder. The installed commands are `media-toolkit` for the desktop and `unidown` for the terminal. `unidown-gui` remains a compatible desktop alias.

The Download **Save as** choices use the same image, audio and video encoders as Convert. It lists compatible formats for the selected media type, including AVIF, WebP, TIFF and JPEG 2000 images, FLAC, WAV and Opus audio, and MKV, MOV and WebM video. **Original** stays recommended when an available source meets the selected limits. Unsupported encoders and unnecessary image copies are omitted; ICO requires a square image up to 256 pixels. An audio/video compatibility conversion can share the source extension: an M4A file can contain ALAC rather than AAC, for example. Conversion checks the actual downloaded codecs before copying or encoding streams. Static-image downloads use white when flattening transparency; Convert also lets you choose black.

Animated GIF, WebP and APNG sources retain their frames in compatible animation outputs. A height limit can resize these sources to GIF, animated WebP, APNG or MP4 when the encoder supports the resulting dimensions. Videos, including social GIFs delivered as MP4, also offer GIF, animated WebP and APNG through the shared conversion engine. The height limit applies consistently to MP4, MKV, MOV, WebM and video-to-animation outputs. Still-image formats are not offered for moving sources. The terminal retains its existing conversion choices, documented below.

## Convert local files

Open **Convert** and click **Add files** to choose up to 100 local media files. The app checks their actual contents and available encoders before showing output formats. Choose a format, review the compatible-file count, select the destination, then click **Convert**. A mixed selection converts only the files compatible with that target; the app shows how many files will be skipped. Eligible completed downloads also have a **Convert file** shortcut.

| Content | Output formats |
| --- | --- |
| Still images | PNG, JPG, WebP, AVIF, BMP, TIFF, ICO, TGA, GIF, JPEG 2000 (JP2) |
| Audio, including audio extracted from video | MP3, WAV, FLAC, M4A, AAC, Opus, Ogg Vorbis, AIFF |
| Video | MP4, MKV, MOV, WebM; GIF, animated WebP and APNG without audio |
| Animation | GIF, animated WebP, APNG, MP4 |

Image inputs include JPEG, PNG, WebP, AVIF, HEIC/HEIF, BMP, TIFF, GIF, ICO, TGA, JPEG 2000, PCX, PNM, DDS, flat PSD, QOI, SGI and XBM when readable by the bundled decoders. Audio/video inputs include common MP3, WAV, FLAC, M4A, AAC, Ogg, Opus, AIFF, MP4, MKV, MOV, WebM, AVI, MPEG, WMV and FLV files. Actual codec support matters more than the filename extension; unreadable files report their own error.

- Conversion runs locally. Static images and image animations work offline; audio/video encoding and MP4 animation output need FFmpeg/ffprobe. Deno is only used by the downloader.
- Original files remain intact. Existing destination files are preserved and repeated names receive a numeric suffix.
- PNG and lossless WebP preserve transparency. JPG and BMP use an explicit white or black background when flattening transparency. GIF uses a 256-color palette, preserves fully transparent pixels and blends partial transparency with the selected background.
- EXIF orientation is applied. Animation preserves its frames and quantizes cumulative timestamps to the output's timing precision: 10 ms for GIF and 1 ms for Pillow WebP/APNG. Frames shorter than one output tick can lengthen playback. Multipage documents are rejected rather than silently reduced to one page. ICO keeps the original dimensions and is offered only for square images up to 256 pixels.
- Image inputs are limited to 128 MiB and 40 million pixels; animations have additional decoded-frame limits. Available formats reflect the source, its streams, encoder dimension limits and installed codecs. Native copies are omitted unless an audio/video compatibility change or a requested resize is needed.
- A file changed after inspection must be added again. Queued tasks keep their own source, target, background and destination snapshots.
- The shared queue supports cancellation, retry and opening completed output folders. Switching tabs preserves selections and running operations.
- Initial format selection stays within one source category. Mixed selections require choosing a format explicitly; audio extraction is never selected automatically for video. Selected formats stay stable while inspecting the same selection.
- Audio/video conversions show measured progress and an estimated remaining time when available. Source preparation and operations with no trustworthy duration remain indeterminate.
- Video-to-animation is an explicit choice in the **Animation** group. It keeps the video dimensions and plays in a loop without audio. GIF uses a palette of up to 256 colors per frame with 1-bit transparency and 10 ms timing; WebP and APNG use lossless frame encoding. There is no desktop 30-second GIF cutoff or automatic frame-rate reduction. Long clips can produce large files; conversion can be cancelled from the queue.

MP4/MKV/MOV use compatible H.264 video and AAC audio; WebM uses VP9 and Opus. Compatible encoded streams are copied when possible, avoiding unnecessary quality loss and processing; incompatible streams are encoded with the existing profiles. The output notes describe which streams are copied or encoded. Video conversions keep the first video and first audio stream. Audio extraction uses the first audio stream, and silent video does not offer audio targets. Local conversion does not preserve every source metadata field; use the downloader's **Original** option when an unchanged available source is wanted.

Encoded video and animation use the displayed orientation and proportions, including rotation and non-square source pixels. Checking video display transforms requires FFmpeg 6 or newer; the portable installer supplies the tested version. Compatible stream copies keep their original display metadata. Variable frame timestamps are retained during video encoding instead of forcing a constant frame rate. See [conversion policy](docs/conversion-policy.md) for the rules shared by both desktop tabs.

## Windows portable release

1. Download the Windows x64 ZIP and `SHA256SUMS.txt` from [GitHub Releases](https://github.com/mighelsterzkdev/media-toolkit/releases).
2. Extract the complete ZIP into a writable folder. Keep `_internal` and `_desktop` beside `unidown.exe` and `media-toolkit.exe`.
3. Open **start.cmd**. Python and administrator access are not required.
4. Image conversion is ready offline. For downloads or audio/video conversion, click **Install tools** if needed. The app downloads pinned FFmpeg/ffprobe and Deno archives directly from their publishers, verifies SHA-256 checksums, and stores them in `.tools` beside the app. Setup reports progress, retries temporary server failures, and explains download, permission, or integrity failures. The terminal prepares missing tools before its first media command.

Downloads go into the extracted folder's `downloads` directory when launched through `start.cmd`. From another terminal directory, either use `--output` or open `start.cmd` to keep that location predictable.

```powershell
# Check the ZIP against the published checksum before extraction
Get-FileHash .\media-toolkit-0.13.1-windows-x64.zip -Algorithm SHA256

# Prepare the external tools without opening the menu
.\unidown.exe --install-tools

# Diagnose the installation; this command does not download tools
.\unidown.exe --doctor
```

To update the portable version, extract a newer **complete** release into a new folder. Keep your downloaded media wherever you prefer. Replacing only the EXE can mix incompatible bundled libraries. Releases are unsigned; checksums verify archive integrity.

## Run from source

### Windows

Install [Python 3.10+](https://www.python.org/downloads/), open **scripts/install.cmd**, then open **start.cmd** in the project root. Setup creates a local `.venv`, installs the tested Python dependency set including the desktop bridge, and prepares the portable tools in `.tools`. The built frontend is committed, so source users do not need Node.js. Neither folder is added to the system PATH. To explicitly run the source desktop while a portable release is present, use `.venv\Scripts\pythonw.exe -B -m universal_downloader.gui`.

To select a specific Python installation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Python 'C:\path\to\python.exe'
```

After updating the project source, run **scripts/update.cmd**. It restores the dependency versions pinned by that release and checks the extractor adapter contract. It deliberately does not install an untested latest yt-dlp version. If a website changes, check for a newer application release.

### Linux and macOS

Install Python 3.10+, [FFmpeg and ffprobe](https://ffmpeg.org/download.html), and a supported [Deno](https://docs.deno.com/runtime/getting_started/installation/) or [Node.js](https://nodejs.org/) runtime on PATH. The Windows installer uses the tested Deno 2.9.7 build; runtime discovery accepts Deno 2.3+ or Node.js 22+.

```bash
python3 -m venv .venv
.venv/bin/python -B -m pip install -c constraints.txt -e .
.venv/bin/python -B -m universal_downloader --doctor
.venv/bin/python -B -m universal_downloader
```

An activated environment also provides the `unidown` command. On Windows, the equivalent Python executable is `.venv\Scripts\python.exe`.

## Choosing what to save in the terminal

```text
MEDIA TOOLKIT
Check a link, then choose the original or another format.

Paste the URL (Enter to exit): https://example.com/animation

Checking the URL...

Title: My animation
Available: Animation

1. Original GIF (recommended)
2. MP4 video (H.264 / AAC)
0. Another URL
Save as [1]: 2

Saved:
.../downloads/My animation [source-identifier] [animation-mp4].mp4
```

The interactive session never starts the requested media download before you choose an output. It may read page data, image bytes, and bounded media probes while checking what is actually available.

- If a page contains several media items, choose the item first. A video, its audio track, and its thumbnail are then separate choices when available.
- Even a single item shows an output menu. Press Enter for the recommended option, or **0** to return to the URL prompt.
- Original video keeps the website's streams; separate video and audio streams are merged without re-encoding.
- Original audio keeps an audio-only source as supplied. When extracted from a video, its encoded audio packets are copied into a compatible container without re-encoding.
- Original images keep their exact bytes, including format, transparency, metadata, and animation. A static thumbnail is labeled separately from primary media.
- Native copies such as MP3 → MP3 and GIF → GIF are omitted from the menu. MP4 remains an explicit compatibility conversion because its source codecs may differ.
- A failed link returns to the URL prompt. Enter on an empty prompt exits; Ctrl+C cancels.

"Original" means what the website makes available, which may already be compressed. It cannot recover the uploader's source quality.

| Content | Optional conversions |
| --- | --- |
| Video | MP4 with H.264 video and AAC audio |
| Audio | MP3 |
| Image or thumbnail | PNG, JPG |
| Animation | GIF, MP4 |

Animation is never silently converted to a still image. GIF re-encoding supports up to **30 seconds**, at 15 fps and up to 720 pixels on the longest side. Longer known animations and conversions requiring unavailable tools are hidden from the menu. Unknown durations are validated after the source is downloaded. Unchanged native GIF downloads have no 30-second conversion limit.

## Links and media discovery

Video/audio extraction uses [yt-dlp](https://github.com/yt-dlp/yt-dlp), with additional image and page discovery. Direct media URLs do not have to end in a recognizable extension: response content and readable media determine what can be offered.

Google image-result and redirect links are resolved to their underlying public source when that URL is exposed. Public pages can expose media through image metadata, image/video elements, responsive `srcset`/`picture` variants, and lazy image attributes. Immediate HTML refresh redirects are followed only on redirect shells; the app does not follow arbitrary related links. When several verified candidates are found, the app offers a numbered choice instead of silently saving a preview. A GIF found through a search result can therefore remain animated or be explicitly converted to MP4.

Special cases include Twitter/X GIF posts, whose native animation is usually an MP4 rather than a GIF file, and TikTok `/music/...` links, which resolve to the exact tagged sound. A TikTok sound is not replaced by a video's mixed soundtrack or cover image.

Search pages and websites vary. A link with no usable public media URL, an expired asset, a login requirement, a region/IP restriction, or a JavaScript-only page can still be unavailable. The app does not use a signed-in browser session. It does not bypass DRM or access controls, process playlists in bulk, or record live streams. A live page may still expose a thumbnail. Site support can change independently of the app.

Images are limited to **32 MB** and **40 million pixels**. Discovery and media probes are bounded; the app does not recursively crawl an entire site. A successful check cannot guarantee that a source remains accessible during the subsequent download.

## Commands and optional limits

Commands with a URL are noninteractive: they save the selected/default original or explicit conversion immediately. The confirmation menu is the behavior of an interactive session started without a URL.

```powershell
# Open the interactive terminal menu
.\.venv\Scripts\python.exe -B -m universal_downloader

# Save the best available original
.\start.cmd "https://example.com/video"

# Save original audio, or convert explicitly to MP3
.\start.cmd "https://example.com/video" --type audio
.\start.cmd "https://example.com/video" --convert mp3

# Convert an animated image to MP4
.\start.cmd "https://example.com/animation.gif" --convert mp4

# Select one item when a page exposes several
.\start.cmd "https://example.com/gallery" --item 2

# Limit source height and source/completed file size
.\start.cmd "https://example.com/video" --max-height 720 --max-size 100MiB

# Choose a folder or show technical diagnostics
.\start.cmd "https://example.com/video" --output "D:\Videos"
.\start.cmd "https://example.com/video" --verbose
```

`--max-height` prefers a source rendition at or below that height. With an explicit video/animation conversion, it can scale the source down when necessary; it does not upscale. Original static images retain their dimensions, and the limit does not apply to audio.

`--max-size` limits the combined selected source bytes (video plus audio when separate) and the actual completed file size. Known sizes are checked before transfer; supported unknown-size transfers stop when download progress exceeds the budget, and the final bytes are checked before publication. This cap supports HTTP/HTTPS downloads, DASH, and ISM. HLS, RTMP/RTSP, and other external streaming protocols are refused when a cap is set because their full transfer cannot be monitored reliably. Remove the cap to use the normal download behavior for those links. The limit is **not** target-size compression or whole-session bandwidth accounting: bounded page/image inspection happens before output selection. Limits are optional and do not add prompts to normal use.

Quote URLs containing `&`. Type names are case-insensitive; `thumbnail` aliases `image`, and `jpeg` aliases `jpg`. Existing `--format video|audio|image` commands alias `--type`. Run `--help` for the complete interface. The portable EXE accepts the same download flags.

## Link queues

In the interactive session, paste several URLs separated by spaces on one line, or type `batch C:\path\to\links.txt` at the URL prompt. The queue shows its item count, lets you choose one output policy, and asks for confirmation before downloading. The normal single-link menu keeps working as before.

For scripts, use a UTF-8 text file with one URL per line. Blank lines and lines starting with `#` are ignored; a UTF-8 BOM is accepted. All entries are validated before any network request. Duplicate URLs are processed once, in their original order. Files are limited to 1 MiB and 1,000 URL entries.

```powershell
# Download each link's default original without interactive prompts
.\start.cmd --batch links.txt

# Apply the same explicit conversion to compatible audio sources
.\start.cmd --batch links.txt --type audio --convert mp3
```

The final summary distinguishes saved, existing, incompatible, and failed items. A requested type or conversion is never replaced by another kind of content. Pages with several media items require a selection; automatic queues report those links instead of guessing. A real failure produces a nonzero exit code, and Ctrl+C stops before the next item. Rerun the same file to reuse verified completed downloads and resume supported interrupted transfers.

## Remembered preferences

Explicit `--output`, `--max-height`, and `--max-size` values are remembered for later launches. The output folder is stored as an absolute path. Command-line values take precedence over saved settings; no saved file means the original defaults. A saved height applies to video/animation and does not block an audio or static-image download. An explicitly supplied height still uses the normal strict validation.

```powershell
# Configure a folder and size cap without downloading anything
.\start.cmd --output "D:\Downloads" --max-size 100MiB --save-preferences

# Inspect or reset saved settings
.\start.cmd --show-preferences
.\start.cmd --reset-preferences

# Ignore saved settings for one command and do not change them
.\start.cmd "https://example.com/image.gif" --no-preferences
```

Settings live in `preferences.json` inside the app's local state directory, alongside the download history. Existing installations retain their legacy `UniversalDownloader` / `universal-downloader` state directories so the new app name keeps their saved preferences and verified history. Only output folder and limits are saved. URLs, cookies, credentials, media types, and conversion choices are not stored as preferences. Choosing a format in one interactive download does not silently convert the next one.

The desktop queue has a separate bounded local state file. It records task URLs, local source/output paths, selected formats and settings needed to restore tasks. It does not store cookies, authentication headers, extractor sessions or embedded media bytes. Pending tasks are restored as stopped, with **Retry** available. Completed entries remain available until **Clear finished** is used; clearing an entry does not delete its media. A second app instance cannot overwrite a queue already owned by another instance. The app explains when queue recovery is unavailable while allowing work to continue.

## Existing files and download history

Files are identified by their source, provider ID or canonical URL, asset role, and requested output settings. A matching title and ID from another source is not enough to count as an existing download.

The local history records completed filenames, sizes, and SHA-256 hashes. A file is reported as **Already downloaded** only after its bytes match that record. Modified, conflicting, or older unverified files are preserved; a new output gets a different filename rather than overwriting them. Files from earlier versions may therefore be downloaded once again under the new naming scheme.

The index lives outside media folders:

- Windows: `%LOCALAPPDATA%\UniversalDownloader\downloads.sqlite3`
- Linux/macOS: `$XDG_STATE_HOME/universal-downloader/downloads.sqlite3`, or `~/.local/state/universal-downloader/downloads.sqlite3`
- Override the directory with `UNIDOWN_DATA_DIR` when needed.

Deleting this index does not delete media, but prevents trusted reuse until a new completed download is recorded. Final outputs are staged before publication, and existing files are preserved under simultaneous saves.

## Interrupted downloads

Supported HTTP transfers keep resumable work in the local state's `resume/` directory, outside the media folder. Paste the same link again with the same output settings to continue after a network interruption, Ctrl+C, or process restart. The page is inspected again to refresh source access.

Before reusing partial bytes, the app checks the selected source URL, HTTP validators, and saved integrity checkpoints. Uncheckpointed bytes after a forced exit may be discarded before continuing. Changed, unverifiable, or unsupported sources restart safely. Servers may refuse continuation even when a previous partial exists. This is not a promise that every site or streaming protocol supports resume.

Only a fully downloaded and validated result is published in the output folder. Completed work is cleaned after publication; resumable failures retain their private state. Concurrent processes cannot write the same work directory simultaneously. The existing size limit includes retained source bytes, and conversion continues from a verified completed source when possible.

```powershell
# Discard inactive partial work when you no longer need it
.\start.cmd --clear-resume
```

This command clears the app's own interrupted work, keeps active downloads, and does not delete completed media or preferences. Without that command, trusted interrupted work remains available for continuation and occupies disk space in the local state directory. If local state is unavailable, downloads can still run with private temporary work and no continuation.

## Development and verification

```powershell
# Deterministic tests using local fixtures
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v

# Check the tested dependency metadata and adapter hooks
.\.venv\Scripts\python.exe -B -m universal_downloader.compatibility

# Explicit real-site checks; no background scheduling
.\.venv\Scripts\python.exe -B scripts/live_smoke.py --output dist/live-smoke.json

# Download, decode, and convert animation examples using temporary storage
.\.venv\Scripts\python.exe -B scripts/live_smoke.py --download --case direct-gif --output dist/live-media.json
```

`constraints.txt` pins the dependency set tested by the Windows/Linux Python 3.10, 3.12, and 3.14 matrix. Adapter tests exercise the provider metadata on which the app relies. Local fixtures check original bytes and audio packet hashes, codecs and animation frames, media discovery, errors, size/height limits, cache verification, races, queues, preferences, and interrupted saves. Resume fixtures exercise actual HTTP Range requests across process restarts, changed source validators, and servers that ignore Range. They do not prove that every live website currently works.

The separate **Live site smoke checks (manual)** workflow runs selected public examples on request and publishes a JSON report. It can inspect metadata only, or download and decode actual files and convert animated examples to MP4. Access restrictions, network conditions, removed examples, and site changes may cause failures; the report states exactly which URLs were checked.

### Build the Windows ZIP

Run on Windows x64 using a separate build environment:

```powershell
python -B -m venv .tools/build-env
.\.tools\build-env\Scripts\python.exe -B -m pip install -c constraints.txt -e ".[desktop]" pyinstaller==6.22.3
.\.tools\build-env\Scripts\python.exe -B scripts/build_windows_gui.py --output dist
.\.tools\build-env\Scripts\python.exe -B scripts/verify_portable.py dist/media-toolkit-0.13.1-windows-x64/unidown.exe --bootstrap
.\.tools\build-env\Scripts\python.exe -B scripts/verify_desktop.py dist/media-toolkit-0.13.1-windows-x64/media-toolkit.exe
```

Use a new output directory for each build. The desktop build produces both executables, their separate library folders, a complete ZIP, and `SHA256SUMS.txt`. `scripts/build_windows.py` remains available for a terminal-only ZIP. The committed frontend assets and notices are included without installing Node.js on the build machine. A source/output fingerprint rejects stale, changed or missing frontend assets before packaging; after editing the interface, run `pnpm install --frozen-lockfile` and `pnpm build` in `desktop-ui`. CI also rebuilds and compares them. The build checks the executable archive to exclude optional Mutagen and build-time PyInstaller modules. The verifier removes Python and external tools from PATH, checks the bundled executable, preserves local image/video originals, converts GIF animation to H.264 MP4, extracts original AAC audio, converts MP3, verifies cached reuse, runs a link queue, checks remembered preferences, and terminates/restarts a real frozen HTTP download to verify continuation. Its temporary download history is isolated from the user's history. The **Build portable Windows release** workflow builds and checks the same artifacts on tagged versions or manual runs; it does not publish a release automatically.

Keep `.venv/` and `.tools/` to avoid reinstalling source dependencies. Downloaded media, generated builds, caches, and package metadata are excluded from Git. Launchers and documented Python commands use `-B` to avoid source bytecode caches.

## License

Project code uses the [MIT license](LICENSE). Dependencies retain their own licenses. The portable ZIP includes Python/library notices, frontend dependency notices, WebView2 SDK notices, and unmodified certifi source. It excludes the optional Mutagen metadata editor and build-time PyInstaller Python modules. External FFmpeg and Deno binaries are downloaded directly from their publishers on first use instead of being redistributed inside the application ZIP. Their pinned release URLs, checksums, and notices are retained locally.
