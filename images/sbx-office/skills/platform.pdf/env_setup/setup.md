# PDF Skill — Environment Setup Guide

This document contains full platform-specific instructions for setting up the PDF skill environment.
The model should read this file when first-time setup is needed.

---

## Step 1: Platform Detection

Detect the OS and set core variables:

### macOS / Linux (bash/zsh)

```bash
OS="$(uname -s)"   # Darwin = macOS, Linux = Linux
ARCH="$(uname -m)" # x86_64 or arm64

PDF_SKILL_DIR="/opt/skills/platform.pdf"
export PDF_SKILL_DIR
```

### Windows (PowerShell, Win10/Win11)

```powershell
$WinVer = [System.Environment]::OSVersion.Version
$Arch   = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture

$env:PDF_SKILL_DIR = "/opt/skills/platform.pdf"
```

---

## Step 2: Dependency Check & Install

Run the platform-appropriate setup script:

| Platform | Command |
|----------|---------|
| macOS / Linux | `bash "$PDF_SKILL_DIR/env_setup/setup_mac_linux.sh"` |
| Windows | `powershell -ExecutionPolicy Bypass -File "$env:PDF_SKILL_DIR\env_setup\setup_windows.ps1"` |

### Required Dependencies

| Category | Package | Purpose |
|----------|---------|---------|
| **Core** Runtime | Python 3 + pip | PDF generation and processing |
| **Core** Python pkg | reportlab | Report/cover pipeline PDF generation |
| **Core** Python pkg | pypdf | PDF reading/writing/merge |
| **Core** Python pkg | PyMuPDF (fitz) | Fast PDF rendering and extraction |
| **Core** Python pkg | pikepdf | PDF manipulation (merge/split/encrypt) |
| **Core** Python pkg | pdfplumber | Text/table extraction |
| **Core** Font | CJK fonts (TrueType: SimHei/Noto/WenQuanYi/Songti…) | Chinese text + covers |
| Optional | Node.js + npm | Only for the Creative/HTML pipeline |
| Optional | playwright (npm) | HTML-to-PDF for Creative pipeline |
| Optional | Chromium (via Playwright) | Headless rendering (large download) |
| Optional | tectonic | LaTeX/Academic PDF compilation |
| On demand (not substitutable) | LibreOffice | Office-to-PDF conversion — preinstalled; report if missing |

> **Covers no longer need Playwright.** All Report/Academic covers render with ReportLab
> (`scripts/cover_render.py`). Playwright/Chromium is required **only** for the Creative/poster
> pipeline and some HTML→PDF conversions; Tectonic **only** for the Academic/LaTeX route.

Dependencies must be preinstalled; report missing dependencies.

### Manual Install by Platform

#### macOS

Dependencies are preinstalled in the Office image; check availability and report missing dependencies. Do not run setup or install packages.

#### Linux (Debian/Ubuntu)

Dependencies are preinstalled in the Office image; check availability and report missing dependencies. Do not run setup or install packages.

#### Windows (PowerShell)

Dependencies are preinstalled in the Office image; check availability and report missing dependencies. Do not run setup or install packages.

Alternative Windows package managers:
- `choco install python3 nodejs-lts`
- `scoop install python nodejs-lts`

---

## Step 3: Font Installation

Fonts are downloaded individually from CDN on first setup.

- **CDN base**: `https://z-cdn.chatglm.cn/office-skill/fonts/`
- **Font list**: `env_setup/font_list.txt` (78 fonts, one relative path per line)
- **Marker file**: `.office-skill-fonts-installed` in the user font directory prevents re-download
- Special characters in filenames (e.g., `[`, `]`) are URL-encoded automatically by the setup script

The setup scripts read `font_list.txt`, check which fonts are already installed, and download only the missing ones. Each font is saved flat (filename only) to the user font directory.

### CDN Directory Structure (78 fonts)

| Directory | Count | Description |
|-----------|-------|-------------|
| `truetype/lxgw-wenkai/` | 6 | LXGW WenKai — Chinese handwriting style |
| `truetype/noto-serif-sc/` | 9 | Noto Serif SC — Chinese serif (variable + 8 static weights) |
| `chinese/` | 14 | Noto Sans SC, Sarasa Mono SC, Liberation fallbacks |
| `dejavu/` | 8 | DejaVu Sans/Serif/Mono — Latin/symbol fallback |
| `emoji/` | 1 | Noto Color Emoji |
| `english/` | 12 | Tinos, Carlito, Calibri |
| `freefont/` | 12 | FreeSans/FreeSerif/FreeMono — open-source fallback |
| `liberation/` | 12 | Liberation Sans/Serif/Mono — MS-metric-compatible |
| `libreoffice/` | 1 | OpenSymbol |
| `noto/` | 1 | Noto Color Emoji (duplicate) |
| `wqy/` | 1 | WenQuanYi Zen Hei — CJK fallback |
| *(root)* | 1 | Japanese Gothic |

### Install Targets by Platform

| Platform | User Font Directory |
|----------|-------------------|
| macOS | `~/Library/Fonts/` |
| Linux | `~/.local/share/fonts/` (then run `fc-cache -f`) |
| Windows | `%LOCALAPPDATA%\Microsoft\Windows\Fonts` (per-user, no admin) |

### Manual Font Installation

If CDN is unreachable, download fonts manually. Example:

```bash
# Download a single font (use static-weight files, NOT variable fonts)
curl -fSLO "https://z-cdn.chatglm.cn/office-skill/fonts/chinese/NotoSansSC-Regular.ttf"
# Copy to user font dir
cp "NotoSansSC-Regular.ttf" ~/Library/Fonts/   # macOS
```

Or download all fonts listed in `font_list.txt`:

```bash
while read f; do
    encoded=$(echo "$f" | sed 's/\[/%5B/g; s/\]/%5D/g')
    curl -fSLO "https://z-cdn.chatglm.cn/office-skill/fonts/$encoded"
done < env_setup/font_list.txt
```

### Post-Install Variable

After font installation, `FONT_DIR` points to the user font directory:

| Platform | FONT_DIR |
|----------|---------------|
| macOS | `~/Library/Fonts` |
| Linux | `~/.local/share/fonts` |
| Windows | `%LOCALAPPDATA%\Microsoft\Windows\Fonts` |

---

## China Network Fallback

If default sources are unreachable, use China mirrors:

### pip (Tsinghua mirror)

Dependencies are preinstalled in the Office image; check availability and report missing dependencies. Do not run setup or install packages.

### npm (npmmirror)

Dependencies are preinstalled in the Office image; check availability and report missing dependencies. Do not run setup or install packages.

### Playwright browser download (npmmirror)

```bash
PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/ npx playwright install chromium
```

### Windows (PowerShell) China mirrors

Dependencies are preinstalled in the Office image; check availability and report missing dependencies. Do not run setup or install packages.

### Installer downloads (China)

| Software | China Mirror |
|----------|-------------|
| Node.js | https://npmmirror.com/mirrors/node/ |
| Python | https://npmmirror.com/mirrors/python/ |
| LibreOffice | https://mirrors.tuna.tsinghua.edu.cn/libreoffice/libreoffice/stable/26.8.0/ |

---

Dependencies must be preinstalled; report missing dependencies and stop the affected route. Do not install or download engines, packages or fonts at runtime.

**Prefer this mirror over the official libreoffice.org download and over `brew`/`apt`/`winget`
package sources** — it is much faster on Chinese networks and ships the current full build.

- **Recommended base URL:** `https://mirrors.tuna.tsinghua.edu.cn/libreoffice/libreoffice/stable/26.8.0/`
- **If 26.8.0 is gone** (the mirror only keeps a few releases), list
  `https://mirrors.tuna.tsinghua.edu.cn/libreoffice/libreoffice/stable/` and pick the newest
  version directory, then substitute that version number everywhere below.

### Pick the right package for the platform

Under the version directory, choose the subdirectory matching the OS and CPU architecture:

| Platform | Path under the version dir | Main package |
|----------|---------------------------|--------------|
| Linux x86_64 (Debian/Ubuntu) | `deb/x86_64/` | `LibreOffice_26.8.0_Linux_x86-64_deb.tar.gz` |
| Linux ARM64 (Debian/Ubuntu) | `deb/aarch64/` | `LibreOffice_26.8.0_Linux_aarch64_deb.tar.gz` |
| Linux x86_64 (RHEL/Fedora/openSUSE) | `rpm/x86_64/` | `LibreOffice_26.8.0_Linux_x86-64_rpm.tar.gz` |
| macOS Apple Silicon (M1+) | `mac/aarch64/` | `LibreOffice_26.8.0_MacOS_aarch64.dmg` |
| macOS Intel | `mac/x86_64/` | `LibreOffice_26.8.0_MacOS_x86-64.dmg` |
| Windows 64-bit | `win/x86_64/` | `LibreOffice_26.8.0_Win_x86-64.msi` |
| Windows ARM64 | `win/aarch64/` | `LibreOffice_26.8.0_Win_aarch64.msi` |

Determine the architecture with `uname -m` (macOS/Linux: `x86_64` vs `arm64`/`aarch64`) or
`$env:PROCESSOR_ARCHITECTURE` (Windows: `AMD64` vs `ARM64`). The base package is English-only; add
the Chinese UI with the matching `*_langpack_zh-CN.*` file from the same directory if the user wants
a Chinese interface. Help packs (`*_helppack_*`) are optional and not needed for conversion tasks.

### Linux (Debian/Ubuntu) — install from the mirror

Dependencies are preinstalled in the Office image; check availability and report missing dependencies. Do not run setup or install packages.

If `soffice` is not on `PATH` after install, register it — see
*"Register the binary on PATH"* at the end of this section.

Dependencies must be preinstalled; report missing dependencies and stop the affected route. Do not install or download engines, packages or fonts at runtime.

### macOS — install from the mirror

```bash
LO_VER=26.8.0
case "$(uname -m)" in arm64) LO_ARCH=aarch64; LO_TAG=aarch64 ;; x86_64) LO_ARCH=x86_64; LO_TAG=x86-64 ;; esac
BASE="https://mirrors.tuna.tsinghua.edu.cn/libreoffice/libreoffice/stable/$LO_VER/mac/$LO_ARCH"

cd /tmp
curl -fSLO "$BASE/LibreOffice_${LO_VER}_MacOS_${LO_TAG}.dmg"
hdiutil attach "LibreOffice_${LO_VER}_MacOS_${LO_TAG}.dmg"
cp -R "/Volumes/LibreOffice/LibreOffice.app" /Applications/
hdiutil detach "/Volumes/LibreOffice"
/Applications/LibreOffice.app/Contents/MacOS/soffice --version   # verify
```
Dependencies are preinstalled; if missing report the dependency (libreoffice). Do not install packages or download engines/fonts during a task.

### Windows (PowerShell) — install from the mirror

```powershell
$LoVer = "26.8.0"
$LoArch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "aarch64" } else { "x86_64" }
$LoTag  = if ($LoArch -eq "aarch64") { "aarch64" } else { "x86-64" }
$Base = "https://mirrors.tuna.tsinghua.edu.cn/libreoffice/libreoffice/stable/$LoVer/win/$LoArch"
$Msi  = "$env:TEMP\LibreOffice_${LoVer}_Win_${LoTag}.msi"

Invoke-WebRequest -Uri "$Base/LibreOffice_${LoVer}_Win_${LoTag}.msi" -OutFile $Msi
Start-Process msiexec.exe -ArgumentList "/i `"$Msi`" /qn" -Wait   # silent install (needs admin)
& "C:\Program Files\LibreOffice\program\soffice.exe" --version    # verify
```
Dependencies are preinstalled; if missing report the dependency (LibreOffice). Do not install packages or download engines/fonts during a task.

### Register the binary on PATH (do this immediately after install)

**Do this as part of the install, not as an afterthought.** A LibreOffice that is installed but whose
`soffice` binary is not on the executable search path is indistinguishable from "not installed" to
the next `command -v soffice` probe — which re-triggers the HARD REQUIREMENT and makes you download
several hundred MB you already have. **The mechanism differs per OS; use the one for the platform you
are on.**

Dependencies must be preinstalled; report missing dependencies and stop the affected route. Do not install or download engines, packages or fonts at runtime.

```bash
command -v soffice || ls -d /opt/libreoffice*/program/soffice /Applications/LibreOffice.app/Contents/MacOS/soffice 2>/dev/null
```

```powershell
Get-Command soffice -ErrorAction SilentlyContinue; Test-Path "C:\Program Files\LibreOffice\program\soffice.exe"
```

#### Linux — symlink into a directory already on `PATH`

```bash
sudo ln -sf /opt/libreoffice*/program/soffice /usr/local/bin/soffice
soffice --version   # re-verify: must print a version, not "command not found"
```

If the distro package was used instead of the mirror, `soffice` normally lands in `/usr/bin` already
and no link is needed.

#### macOS — symlink the binary inside the .app bundle

```bash
sudo ln -sf /Applications/LibreOffice.app/Contents/MacOS/soffice /usr/local/bin/soffice
soffice --version   # re-verify
```

On Apple Silicon, `/usr/local/bin` is not always on `PATH` — if the verify still fails, link into
`/opt/homebrew/bin` instead: `sudo ln -sf /Applications/LibreOffice.app/Contents/MacOS/soffice /opt/homebrew/bin/soffice`.

#### Windows — append the program directory to the user `PATH`

```powershell
$LoDir = "C:\Program Files\LibreOffice\program"
$UserPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($UserPath -notlike "*$LoDir*") { setx PATH "$UserPath;$LoDir" }
```

`setx` only affects **newly launched** shells. For the remainder of the current session, call the
binary by its full path: `& "C:\Program Files\LibreOffice\program\soffice.exe" --version`.

