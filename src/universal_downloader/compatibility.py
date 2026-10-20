"""The dependency contract verified by this application's test matrix."""

from importlib import metadata
import re

TESTED_VERSIONS = {"yt-dlp": "2026.8.19", "yt-dlp-ejs": "0.8.0", "Pillow": "12.3.0", "pi-heif": "1.4.0"}


def _version(value: str) -> str:
    # PEP 440 normalizes calendar versions such as 2026.08.19 to 2026.8.19.
    return re.sub(r"(?<!\d)0+(\d+)", r"\1", value)


def compatibility_issues() -> list[str]:
    """Report untested packages or missing extractor hooks before an update is accepted."""
    issues = []
    for name, expected in TESTED_VERSIONS.items():
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            issues.append(f"{name} is missing (tested: {expected}).")
            continue
        if _version(actual) != _version(expected):
            issues.append(f"{name} {actual} is untested (tested: {expected}).")
    try:
        from yt_dlp.extractor.twitter import TwitterIE
        from yt_dlp.extractor.tiktok import TikTokBaseIE, TikTokIE
        required = (
            (TwitterIE, "_extract_status"), (TwitterIE, "_real_extract"),
            (TikTokBaseIE, "_get_universal_data"), (TikTokBaseIE, "_create_url"),
            (TikTokIE, "_extract_web_data_and_status"),
        )
        for owner, name in required:
            if not callable(getattr(owner, name, None)):
                issues.append(f"yt-dlp adapter hook {owner.__name__}.{name} is missing.")
    except ImportError as error:
        issues.append(f"yt-dlp extractor metadata could not be imported: {error}")
    return issues


def main() -> int:
    issues = compatibility_issues()
    if issues:
        print("Dependency compatibility check failed:")
        for issue in issues:
            print(f"- {issue}")
        print("Run update.cmd to restore the tested dependencies.")
        return 1
    print("Dependency versions and extractor adapter hooks match the tested release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
