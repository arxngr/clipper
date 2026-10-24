"""Install portable Windows tools into .tools; other systems can use their package manager."""

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".tools"


def fetch(url):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "video-downloader-setup"}),
        timeout=60,
    )


def install(repository: str, asset_name: str, executables: tuple[str, ...]):
    if all((TOOLS / name).is_file() for name in executables):
        print(f"Already installed: {', '.join(executables)}", flush=True)
        return
    with fetch(
        f"https://api.github.com/repos/{repository}/releases/latest"
    ) as response:
        release = json.load(response)
    asset = next(
        (
            asset
            for asset in release["assets"]
            if re.fullmatch(asset_name, asset["name"])
        ),
        None,
    )
    if not asset:
        raise SystemExit(
            f"Could not find {asset_name} in {repository}'s latest release."
        )
    print(f"Downloading {repository}: {asset_name}", flush=True)
    with tempfile.TemporaryDirectory(prefix="downloader-tools-") as temporary:
        archive = Path(temporary) / "tools.zip"
        with (
            fetch(asset["browser_download_url"]) as response,
            archive.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
        digest = asset.get("digest")
        if digest and digest.startswith("sha256:"):
            with archive.open("rb") as checksum_input:
                actual = hashlib.file_digest(checksum_input, "sha256").hexdigest()
            if actual != digest.split(":", 1)[1]:
                raise SystemExit(
                    "Downloaded tool checksum did not match GitHub's release metadata."
                )
        with zipfile.ZipFile(archive) as package:
            for executable in executables:
                member = next(
                    name
                    for name in package.namelist()
                    if name.split("/")[-1] == executable
                )
                with (
                    package.open(member) as source,
                    (TOOLS / executable).open("wb") as output,
                ):
                    shutil.copyfileobj(source, output)
    print(f"Installed: {', '.join(executables)}", flush=True)


if __name__ == "__main__":
    if os.name != "nt":
        raise SystemExit(
            "Install ffmpeg (including ffprobe) and Deno with your package manager, then put them on PATH."
        )
    TOOLS.mkdir(exist_ok=True)
    install(
        "BtbN/FFmpeg-Builds",
        r"ffmpeg-N-.*-win64-gpl\.zip",
        ("ffmpeg.exe", "ffprobe.exe"),
    )
    install("denoland/deno", r"deno-x86_64-pc-windows-msvc\.zip", ("deno.exe",))
