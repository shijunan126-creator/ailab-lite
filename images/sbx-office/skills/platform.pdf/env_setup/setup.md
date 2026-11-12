# PDF Skill — Environment

In this sandbox every dependency of the pdf skill is preinstalled in the image; nothing is installed or downloaded at runtime. Run `bash "$PDF_SKILL_DIR/env_setup/env_check.sh"` to confirm. If it reports something missing, that is an environment fault: report it to the user and stop the route that needs it; do not install it or substitute another program.

## Installed dependencies

| Category | Package | Purpose |
|----------|---------|---------|
| **Core** Runtime | Python 3 | PDF generation and processing |
| **Core** Python pkg | reportlab | Report/cover pipeline PDF generation |
| **Core** Python pkg | pypdf | PDF reading/writing/merge |
| **Core** Python pkg | PyMuPDF (fitz) | Fast PDF rendering and extraction |
| **Core** Python pkg | pikepdf | PDF manipulation (merge/split/encrypt) |
| **Core** Python pkg | pdfplumber | Text/table extraction |
| **Core** CLI | qpdf, Ghostscript (`gs`), Poppler (`pdftoppm`, `pdftotext`, `pdffonts`) | Split/merge/linearize, compression, rendering |
| HTML route | Node.js, playwright (npm), Chromium (`$PLAYWRIGHT_CHROMIUM_PATH`) | Creative/poster HTML → PDF |
| Academic route | XeLaTeX (TeX Live, offline) | `pdf.py convert.latex` |
| Office → PDF | LibreOffice (`soffice`) | Not substitutable |

## Fonts

All fonts are installed system-wide (`fc-list` lists every file); nothing is downloaded at runtime.

| Family (name to use) | Covers | File |
|---|---|---|
| Noto Sans CJK SC / Noto Serif CJK SC | Chinese sans / serif for LibreOffice, Chromium, matplotlib | `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`, `NotoSerifCJK-Regular.ttc` |
| WenQuanYi Zen Hei (+ Mono, Sharp) | Chinese; the TrueType CJK font ReportLab can embed (`subfontIndex` 0/1/2) | `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc` |
| Liberation Serif / Sans / Mono | Times New Roman / Arial / Courier New, same metrics | `/usr/share/fonts/truetype/liberation/` |
| Carlito / Caladea | Calibri / Cambria, same metrics | `/usr/share/fonts/truetype/crosextra/` |
| DejaVu Sans / Serif / Mono, FreeSerif / FreeSans / FreeMono | Latin, symbols | `/usr/share/fonts/truetype/dejavu/`, `/usr/share/fonts/truetype/freefont/` |
| Inter (Inter Variable), Material Icons, Noto Color Emoji | HTML routes | `/usr/share/fonts/truetype/inter-vf/`, `material-design-icons-iconfont/`, `noto/NotoColorEmoji.ttf` |

Documents may keep proprietary names (微软雅黑, 宋体, 黑体, 仿宋, 楷体, SimSun, SimHei, Calibri, Times New Roman, Arial): fontconfig maps them to the fonts above, so LibreOffice and Chromium render them; the file keeps the name the user's Office will use. ReportLab and matplotlib take files or their own font names, not fontconfig aliases — use the files above.
