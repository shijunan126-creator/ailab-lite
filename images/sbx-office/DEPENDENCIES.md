# Office production dependency closure

System packages are frozen by Debian snapshot `20261101T000000Z` and the base image digest. Python distributions and transitive dependencies are version/hash locked in requirements.lock; Node dependencies and integrity hashes are in package-lock.json. The signed snapshot index selects every version and SHA256. Bounded streaming downloads first try the identical package path at deb.debian.org, then the fixed snapshot; every package must match the snapshot SHA256/size before offline APT installation. There is no fallback to newer package versions. Acquisition has a 20-minute total deadline, 20-second socket timeout, 64KiB buffers, and at most two source attempts per package; failed partial files are removed. Rebuilds install dependencies only at image build time. Work executes with no network; missing packages are errors. Chromium, LibreOffice and XeLaTeX are short-lived job processes, with no new resident service.

| Route | Preinstalled dependencies | License family |
| --- | --- | --- |
| Word | docx (Node), python-docx, defusedxml, lxml, Pandoc | MIT, BSD, PSF, GPL |
| Excel | openpyxl, XlsxWriter, pandas, numpy, LibreOffice | MIT, BSD, MPL/LGPL |
| Slides | pptxgenjs, python-pptx, sharp, image-size | MIT, Apache-2.0 |
| PDF | ReportLab, pypdf, pikepdf, pdfplumber/pdfminer, PyMuPDF, pypdfium2, Ghostscript (`gs`), qpdf CLI | BSD, MPL-2.0, MIT, AGPL-3.0, Apache-2.0 |
| HTML | Playwright, system Chromium, pdf-lib, KaTeX, Mermaid | Apache-2.0, BSD, MIT |
| Academic | offline TeX Live/XeLaTeX and installed macro/font collections | mixed LPPL/GPL/font licenses; distribution notices remain installed |
| OCR / conversion | Tesseract, Poppler, ImageMagick, markitdown | Apache-2.0, GPL, ImageMagick, MIT |
| Fonts | Noto CJK/Serif CJK, WenQuanYi, AR PL (via TeX Live), Liberation, Carlito/Caladea, DejaVu, FreeFont, Inter, Material Icons, Noto Color Emoji | OFL-1.1, GPL with font exception, Apache-2.0, Arphic, Bitstream Vera |

PyMuPDF and Ghostscript are AGPL-3.0: image redistribution must retain its corresponding source/license obligations (or use a separately obtained commercial license). Installing them is not a claim of permissive-only licensing. Package notices remain in site-packages, node_modules and /usr/share/doc; original skill and runtime licenses remain in vendor and generated packages.

The academic adapter uses offline XeLaTeX instead of the source's platform-specific/network-fetching engine. It disables shell escape and bounds each compilation to 120 seconds. Unsupported macro packages and Windows Excel/COM/VBA execution remain explicit external boundaries. Proprietary office fonts are not shipped: fontconfig maps their names to metric-compatible Liberation/Carlito/Caladea and to Noto CJK (`binding="same"`, which Chromium requires), and the skills register embeddable files by path (ReportLab cannot embed Noto CJK's CFF outlines, so it uses WenQuanYi and AR PL). Proprietary font fidelity is not claimed.
