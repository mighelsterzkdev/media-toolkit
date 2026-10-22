# Desktop conversion policy

Download and Convert use the same inspected-media plans. A format is offered
only when the content, selected limits and installed encoder/muxer can support
it. Download inspection is provisional until the fetched bytes are checked.
An extension alone never proves codec compatibility.

## Original and compatibility outputs

- Original preserves the available source and is recommended when it satisfies
  the selected limits. It does not silently resize or re-encode.
- MP4/MKV/MOV compatibility means H.264 with AAC; WebM means VP9 with Opus.
  M4A/AAC means AAC-LC. A native container can still need this conversion.
- Compatible streams are copied; only incompatible streams or requested video
  resizes are encoded. PCM extraction preserves compatible source bit depth.
- Same-format local no-ops are omitted. Download compatibility choices can
  remain available because provider metadata does not prove the actual codec.
- Audio extraction is explicit. Video-to-animation is explicit and omits audio.
  Mixed local selections require choosing a format before conversion.

## Geometry and timing

- Stream copies retain their display metadata. Encoded video applies rotation,
  reflection and pixel aspect ratio once, then writes square-pixel output.
- Height limits use displayed dimensions. Video profiles pad to even pixels
  inside the limit. A limit below two pixels cannot produce these video profiles.
- Output dimensions must satisfy both resource limits and the specific encoder's
  limits. A format with a smaller maximum dimension does not hide other formats.
- Video encoding preserves variable frame timestamps. Image animation export
  quantizes cumulative timestamps instead of rounding each frame independently.
  GIF uses 10 ms ticks; Pillow WebP/APNG use 1 ms ticks. Each frame needs at least
  one tick, so faster inputs can play more slowly in those outputs.
- Per-frame delays also have encoder limits. The current GIF writer accepts
  655,350 ms, WebP 16,777,215 ms, and Pillow APNG 65,535 ms. Animation-to-MP4 uses
  that checked APNG intermediate. Local targets exceeding these bounds are
  omitted rather than truncating timing or splitting frames.
- GIF has palette and transparency limits. Lossless animation encoding cannot
  restore detail already lost in a video source. Profiles describe relevant
  losses, including audio omission and transparency handling.
- VP8/VP9 WebM alpha uses the libvpx decoder when encoding, since the native
  decoder omits the separate alpha plane. Encoded opaque video profiles use the
  chosen white/black background. Compatible alpha stream copies keep their
  encoded packets and do not need a background.
- Transparent video-to-WebP uses FFmpeg APNG followed by the existing Pillow
  animation adapter. This avoids incorrect overlapping alpha in FFmpeg's
  per-frame WebP muxer while preserving variable timing. It inherits the image
  animation bounds: 1,000 frames, 80 million decoded pixels and a 128 MiB
  intermediate. Other video-to-animation paths remain streaming.

## Publication and verification

Sources are inspected by content and checked again from a private snapshot.
Source mutations invalidate the task. Work is published only after conversion
and limits validation succeed; source files and existing destination files are
preserved. Cancellation stops contained child processes and removes private work.

Regression tests use real encoders, decoded frames, packet hashes, frame delays,
orientation metadata and HTTP transfers. The Windows release also verifies its
frozen workers. Frontend source/output fingerprints prevent packaging an older
interface with newer backend code. These checks cover the tested fixtures; site
access and arbitrary codec support can still change or fail.
