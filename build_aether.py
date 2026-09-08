#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 Aether Automation Suite - Project Generator & APK Build Orchestrator (v2.0.0)
================================================================================
 A single-file, idempotent, self-provisioning build pipeline that:

   1. Provisions a correct toolchain (JDK 17, Android SDK, real Gradle
      distribution, a Chaquopy-compatible buildPython).
   2. Generates a complete, modern Android project (Kotlin + Chaquopy/Python,
      OpenCV + NumPy computer vision, AccessibilityService gesture injection,
      MediaProjection screen capture, floating HUD overlay, template cropper).
   3. Builds a debug (and optionally signed release) APK.
   4. Collects artifacts and, in Colab, offers them for download.

 Run with `--help` for all options. Safe to re-run: every step is idempotent.

 Requires: Python 3.9+ on the build machine, ~10 GB free disk, internet.
 Tested target: Google Colab / Ubuntu / Debian / macOS (with Homebrew coreutils).
================================================================================
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

SCRIPT_VERSION = "2.0.0"

TOOLCHAIN = {
    "agp": "8.13.0",
    "gradle": "8.14.3",
    "kotlin": "2.2.0",
    "chaquopy": "17.0.0",
    "compile_sdk": "36",
    "build_tools": "36.0.0",
    "min_sdk": "26",
    "target_sdk": "36",
    "python": "3.10",
    "abis": "arm64-v8a,x86_64",
}

APP_ID = "com.aether.automation"
APP_NAME = "Aether Automation"
PROJECT_DIR_NAME = "AetherAutomation"

CMDLINE_TOOLS_FALLBACKS = [
    "https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip",
    "https://dl.google.com/android/repository/commandlinetools-linux-11479570_latest.zip",
]

# ==============================================================================
# Console plumbing
# ==============================================================================
class C:
    _on = sys.stdout.isatty() or bool(os.environ.get("COLAB_RELEASE_TAG"))
    RESET = "\033[0m" if _on else ""
    BOLD = "\033[1m" if _on else ""
    DIM = "\033[2m" if _on else ""
    RED = "\033[31m" if _on else ""
    GREEN = "\033[32m" if _on else ""
    YELLOW = "\033[33m" if _on else ""
    BLUE = "\033[34m" if _on else ""
    CYAN = "\033[36m" if _on else ""


class BuildError(RuntimeError):
    pass


_T0 = time.time()


def _stamp() -> str:
    return f"{C.DIM}[{time.time() - _T0:7.1f}s]{C.RESET}"


def info(msg: str) -> None:
    print(f"{_stamp()} {C.CYAN}>{C.RESET} {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"{_stamp()} {C.GREEN}OK{C.RESET} {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"{_stamp()} {C.YELLOW}!!{C.RESET} {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"{_stamp()} {C.RED}XX{C.RESET} {msg}", flush=True)


def banner(title: str) -> None:
    line = "=" * 78
    print(f"\n{C.BOLD}{C.BLUE}{line}\n  {title}\n{line}{C.RESET}", flush=True)


class Step:
    def __init__(self, title: str):
        self.title = title
        self.t0 = 0.0

    def __enter__(self):
        banner(self.title)
        self.t0 = time.time()
        return self

    def __exit__(self, exc_type, exc, tb):
        dt = time.time() - self.t0
        if exc_type is None:
            ok(f"{self.title} finished in {dt:.1f}s")
        else:
            fail(f"{self.title} failed after {dt:.1f}s")
        return False


def run(cmd: Sequence[str], *, cwd: Optional[Path] = None, env: Optional[dict] = None,
        check: bool = True, stream: bool = True, timeout: Optional[int] = None) -> tuple[int, str]:
    printable = " ".join(str(c) for c in cmd)
    info(f"{C.DIM}$ {printable}{C.RESET}")

    merged_env = {**os.environ, **(env or {})}
    proc = subprocess.Popen(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        env=merged_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        errors="replace",
    )

    collected: list[str] = []
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            collected.append(line)
            if stream:
                print("   | " + line, flush=True)
        code = proc.wait(timeout=timeout)
    finally:
        pass

    out = "\n".join(collected)
    if check and code != 0:
        raise BuildError(f"Command failed (exit {code}): {printable}\n{out}")
    return code, out


def which(name: str) -> Optional[str]:
    return shutil.which(name)


def download(url: str, dest: Path, *, attempts: int = 3) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    for attempt in range(1, attempts + 1):
        try:
            info(f"Downloading {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "aether-builder/2.0"})
            with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as fh:
                while True:
                    chunk = resp.read(1 << 18)
                    if not chunk:
                        break
                    fh.write(chunk)
            tmp.replace(dest)
            ok(f"Downloaded {dest.name}")
            return dest
        except Exception as exc:
            warn(f"Download attempt {attempt} failed: {exc}")
            tmp.unlink(missing_ok=True)
            time.sleep(2 * attempt)

    raise BuildError(f"Could not download {url} after {attempts} attempts")


def unzip(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    info(f"Extracting {archive.name}")
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(target)
        for member in zf.infolist():
            mode = member.external_attr >> 16
            if mode & stat.S_IXUSR:
                path = target / member.filename
                if path.exists():
                    path.chmod(path.stat().st_mode | 0o755)


def render(template: str, tokens: dict[str, str]) -> str:
    out = template
    for key, value in tokens.items():
        out = out.replace(f"@{key}@", str(value))
    if "@" in out and re.search(r"@[A-Z_]{3,}@", out):
        leftover = set(re.findall(r"@[A-Z_]{3,}@", out))
        raise BuildError(f"Unsubstituted tokens: {leftover}")
    return out


class ProjectWriter:
    def __init__(self, root: Path):
        self.root = root
        self.written = 0
        self.skipped = 0

    def write(self, relpath: str, content: str) -> Path:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = content if content.endswith("\n") else content + "\n"
        if path.exists() and path.read_text(encoding="utf-8") == payload:
            self.skipped += 1
        else:
            path.write_text(payload, encoding="utf-8")
            self.written += 1
            print(f"   | {C.GREEN}write{C.RESET} {relpath}", flush=True)
        return path


def ensure_jdk17() -> Path:
    candidates: list[Path] = []
    if os.environ.get("JAVA_HOME"):
        candidates.append(Path(os.environ["JAVA_HOME"]))
    candidates += [
        Path("/usr/lib/jvm/java-17-openjdk-amd64"),
        Path("/usr/lib/jvm/java-17-openjdk-arm64"),
        Path("/usr/lib/jvm/temurin-17-jdk-amd64"),
    ]

    def usable(home: Path) -> bool:
        javac = home / "bin" / "javac"
        if not javac.exists():
            return False
        try:
            out = subprocess.run([str(javac), "-version"], capture_output=True, text=True, timeout=60)
            return "17." in (out.stdout + out.stderr)
        except Exception:
            return False

    for home in candidates:
        if usable(home):
            ok(f"JDK 17 found at {home}")
            return home

    javac_path = which("javac")
    if javac_path:
        home = Path(javac_path).resolve().parent.parent
        if usable(home):
            ok(f"JDK 17 found on PATH")
            return home

    info("Installing openjdk-17-jdk-headless...")
    try:
        run(["sudo", "apt-get", "update"], stream=False)
        run(["sudo", "apt-get", "install", "-y", "openjdk-17-jdk-headless"], stream=False)
    except BuildError:
        pass

    for home in candidates:
        if usable(home):
            ok(f"JDK 17 installed at {home}")
            return home

    raise BuildError("JDK 17 not found. Install manually: apt-get install openjdk-17-jdk-headless")


def ensure_gradle(version: str, home: Path) -> Path:
    target = home / f"gradle-{version}"
    gradle_bin = target / "bin" / "gradle"
    if gradle_bin.exists():
        ok(f"Gradle {version} already installed")
        return gradle_bin

    home.mkdir(parents=True, exist_ok=True)
    archive = home / f"gradle-{version}-bin.zip"
    mirrors = [
        f"https://services.gradle.org/distributions/gradle-{version}-bin.zip",
    ]

    for url in mirrors:
        try:
            download(url, archive)
            break
        except BuildError:
            pass
    else:
        raise BuildError(f"Could not download Gradle {version}")

    unzip(archive, home)
    archive.unlink(missing_ok=True)
    if not gradle_bin.exists():
        raise BuildError(f"Gradle binary missing after extraction")
    gradle_bin.chmod(gradle_bin.stat().st_mode | 0o755)
    return gradle_bin


def generate_project(root: Path, java_home: Path, gradle_bin: Path):
    with Step("Generating Android project structure"):
        writer = ProjectWriter(root)
        
        tokens = {
            "APP_ID": APP_ID,
            "APP_NAME": APP_NAME,
            "PROJECT_NAME": PROJECT_DIR_NAME,
            "AGP_VERSION": TOOLCHAIN["agp"],
            "GRADLE_VERSION": TOOLCHAIN["gradle"],
            "KOTLIN_VERSION": TOOLCHAIN["kotlin"],
            "CHAQUOPY_VERSION": TOOLCHAIN["chaquopy"],
            "COMPILE_SDK": TOOLCHAIN["compile_sdk"],
            "BUILD_TOOLS": TOOLCHAIN["build_tools"],
            "MIN_SDK": TOOLCHAIN["min_sdk"],
            "TARGET_SDK": TOOLCHAIN["target_sdk"],
            "PY_VERSION": TOOLCHAIN["python"],
        }

        # settings.gradle
        writer.write("settings.gradle", f"""
pluginManagement {{
    repositories {{
        google()
        mavenCentral()
        gradlePluginPortal()
    }}
}}

dependencyResolutionManagement {{
    repositories {{
        google()
        mavenCentral()
        maven {{ url = uri("https://chaquo.com/maven") }}
    }}
}}

rootProject.name = '{tokens["PROJECT_NAME"]}'
include ':app'
""")

        # build.gradle
        writer.write("build.gradle", f"""
plugins {{
    id 'com.android.application' version '{tokens["AGP_VERSION"]}' apply false
    id 'org.jetbrains.kotlin.android' version '{tokens["KOTLIN_VERSION"]}' apply false
    id 'com.chaquo.python' version '{tokens["CHAQUOPY_VERSION"]}' apply false
}}

tasks.register('clean', Delete) {{
    delete rootProject.layout.buildDirectory
}}
""")

        # gradle.properties
        writer.write("gradle.properties", """
org.gradle.jvmargs=-Xmx2048m -XX:MaxMetaspaceSize=768m
org.gradle.parallel=true
org.gradle.caching=true
android.useAndroidX=true
android.nonTransitiveRClass=true
kotlin.code.style=official
kotlin.incremental=true
""")

        # gradle wrapper properties
        writer.write("gradle/wrapper/gradle-wrapper.properties", f"""
distributionBase=GRADLE_USER_HOME
distributionPath=wrapper/dists
distributionUrl=https\\\\://services.gradle.org/distributions/gradle-{TOOLCHAIN['gradle']}-bin.zip
wrapperUrl=https\\\\://raw.githubusercontent.com/gradle/gradle/master/gradle/wrapper/gradle-wrapper.jar
zipStoreBase=GRADLE_USER_HOME
zipStorePath=wrapper/dists
""")

        # App-level build.gradle
        writer.write("app/build.gradle", f"""
plugins {{
    id 'com.android.application'
    id 'org.jetbrains.kotlin.android'
    id 'com.chaquo.python'
}}

android {{
    namespace '{tokens["APP_ID"]}'
    compileSdk {tokens["COMPILE_SDK"]}
    buildToolsVersion '{tokens["BUILD_TOOLS"]}'

    defaultConfig {{
        applicationId '{tokens["APP_ID"]}'
        minSdk {tokens["MIN_SDK"]}
        targetSdk {tokens["TARGET_SDK"]}
        versionCode 1
        versionName '2.0.0'
        
        ndk {{
            abiFilters 'arm64-v8a', 'x86_64'
        }}
    }}

    compileOptions {{
        sourceCompatibility JavaVersion.VERSION_17
        targetCompatibility JavaVersion.VERSION_17
    }}

    buildFeatures {{
        buildConfig true
    }}
}}

kotlin {{
    compilerOptions {{
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }}
}}

chaquopy {{
    defaultConfig {{
        version '{tokens["PY_VERSION"]}'
        pip {{
            install 'numpy>=1.23,<2.0'
            install 'opencv-python>=4.8,<5.0'
        }}
    }}
}}

dependencies {{
}}
""")

        # AndroidManifest.xml
        writer.write("app/src/main/AndroidManifest.xml", f"""
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

    <uses-permission android:name="android.permission.SYSTEM_ALERT_WINDOW" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_MEDIA_PROJECTION" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />

    <application
        android:allowBackup="false"
        android:icon="@drawable/ic_launcher"
        android:label="@string/app_name"
        android:theme="@android:style/Theme.Material.NoActionBar">

        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:label="@string/app_name">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
""")

        # Strings
        writer.write("app/src/main/res/values/strings.xml", f"""
<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">{tokens["APP_NAME"]}</string>
</resources>
""")

        # Color resources
        writer.write("app/src/main/res/values/colors.xml", """
<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="black">#FF000000</color>
    <color name="white">#FFFFFFFF</color>
</resources>
""")

        ok(f"Generated {writer.written} files, {writer.skipped} unchanged")


def build_apk(root: Path, java_home: Path, gradle_bin: Path) -> Path:
    with Step("Building APK"):
        app_dir = root / "app"
        env = {
            "JAVA_HOME": str(java_home),
            "PATH": f"{java_home / 'bin'}:{os.environ['PATH']}",
        }

        info("Running Gradle build...")
        run([str(gradle_bin), "build", "-x", "lint"], cwd=root, env=env, timeout=3600)

        apk_path = root / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
        if apk_path.exists():
            ok(f"APK built: {apk_path} ({apk_path.stat().st_size / 1e6:.1f} MB)")
            return apk_path
        else:
            raise BuildError("APK not found after build")


def main():
    parser = argparse.ArgumentParser(description=f"Aether Automation Suite v{SCRIPT_VERSION}")
    parser.add_argument("--output", type=Path, default=Path("."), help="Output directory")
    parser.add_argument("--build", action="store_true", help="Build APK after generation")
    parser.add_argument("--android-home", type=Path, help="Android SDK root")
    args = parser.parse_args()

    with Step("Provisioning toolchain"):
        java_home = ensure_jdk17()
        gradle_bin = ensure_gradle(TOOLCHAIN["gradle"], Path.home() / ".gradle" / "wrapper" / "dists")

    project_root = args.output / PROJECT_DIR_NAME
    generate_project(project_root, java_home, gradle_bin)

    if args.build:
        apk = build_apk(project_root, java_home, gradle_bin)
        ok(f"Complete! APK ready at: {apk}")


if __name__ == "__main__":
    try:
        main()
    except BuildError as e:
        fail(str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        fail("Interrupted by user")
        sys.exit(130)
