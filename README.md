# epub2cbr

[![CI](https://github.com/romancin/epub2cbr/actions/workflows/ci.yml/badge.svg)](https://github.com/romancin/epub2cbr/actions/workflows/ci.yml)
[![Release](https://github.com/romancin/epub2cbr/actions/workflows/release.yml/badge.svg)](https://github.com/romancin/epub2cbr/actions/workflows/release.yml)
[![GitHub release](https://img.shields.io/github/v/release/romancin/epub2cbr)](https://github.com/romancin/epub2cbr/releases)

Convert EPUB and PDF comic/manga files to CBR (Comic Book RAR) archives.

## Features

- **EPUB support** with automatic structure detection for optimal conversion
- **PDF support** with high-quality page rendering
- **High-quality output** with automatic resolution scaling
- **CBR creation** ready for comic book readers
- Preserves reading order from EPUB metadata

## Quick Start

```bash
# Using Docker (recommended - no dependencies needed)
docker run --rm -v $(pwd):/data ghcr.io/romancin/epub2cbr:latest comic.epub --cbr-only
docker run --rm -v $(pwd):/data ghcr.io/romancin/epub2cbr:latest comic.pdf --cbr-only

# Or install locally with mise
mise install && mise run setup-all
./epub2cbr.py comic.epub --cbr-only
./epub2cbr.py comic.pdf --cbr-only
```

## Installation

### Using Docker (recommended)

No dependencies needed - everything is included in the container.

```bash
# Pull the image from GitHub Container Registry
docker pull ghcr.io/romancin/epub2cbr:latest

# Convert EPUB
docker run --rm  -it -v $(pwd):/data ghcr.io/romancin/epub2cbr:latest manga.epub --cbr-only

# Convert PDF
docker run --rm  -it -v $(pwd):/data ghcr.io/romancin/epub2cbr:latest comic.pdf --cbr-only

# Batch conversion (EPUB and PDF)
for f in *.epub *.pdf; do docker run --rm -it -v $(pwd):/data ghcr.io/romancin/epub2cbr:latest "$f" --cbr-only; done

# Or build locally
docker build -t epub2cbr .
docker run --rm -it -v $(pwd):/data epub2cbr manga.epub --cbr-only
```

### Using mise

[mise](https://mise.jdx.dev/) automatically manages Python, Go, and dependencies.

```bash
git clone https://github.com/romancin/epub2cbr.git
cd epub2cbr
mise install
mise run setup-all
```

### Manual installation

```bash
git clone https://github.com/romancin/epub2cbr.git
cd epub2cbr
pip install -r requirements.txt
go install github.com/sensepost/gowitness@latest
brew install rar  # macOS (or: sudo apt install rar on Linux/WSL)
```

## Usage

```bash
# Basic EPUB conversion (auto-detects best method)
./epub2cbr.py comic.epub --cbr-only

# PDF conversion
./epub2cbr.py comic.pdf --cbr-only

# PDF with custom DPI (default: 200)
./epub2cbr.py comic.pdf --cbr-only --dpi 300

# Batch conversion (EPUB and PDF)
for f in *.epub *.pdf; do ./epub2cbr.py "$f" --cbr-only; done

# Keep intermediate images
./epub2cbr.py comic.epub --cbr

# Custom JPEG quality (default: 92)
./epub2cbr.py comic.epub --cbr-only --jpeg-quality 85

# Keep PNG format (larger files, lossless)
./epub2cbr.py comic.epub --cbr-only --no-jpeg
```

### Advanced options

| Option | Description |
|--------|-------------|
| `-m extract` | Force direct image extraction (EPUB only) |
| `-m screenshot` | Force screenshot mode with gowitness (EPUB only) |
| `--dpi N` | DPI for PDF rendering (default: 200) |
| `--threads N` | Parallel threads for screenshot mode (default: 4) |
| `--keep-extracted` | Keep extracted EPUB files |
| `--timeout N` | Screenshot timeout in seconds |
| `--check-deps` | Verify all dependencies |

## How it works

### EPUB conversion

The tool analyzes EPUB structure to choose the best conversion method:

| EPUB Type | Detection | Method |
|-----------|-----------|--------|
| Text embedded in images | Empty `TextContainer` | Direct extraction |
| Text in HTML overlay | `TextContainer` has content | Screenshot rendering |

### PDF conversion

PDF pages are rendered to high-quality images using PyMuPDF at the specified DPI (default: 200).

## Requirements

| Dependency | Required | Purpose |
|------------|----------|---------|
| Python 3 | Yes | Core runtime |
| Pillow | Yes | Image processing |
| PyMuPDF | Only for PDF | PDF page rendering |
| gowitness | Only for HTML EPUB | Screenshot capture |
| rar | Only for CBR | Archive creation |

## Troubleshooting

**Check dependencies:**
```bash
./epub2cbr.py --check-deps
```

**gowitness not found:**
```bash
go install github.com/sensepost/gowitness@latest
export PATH=$PATH:$(go env GOPATH)/bin
```

**rar not found:**
```bash
brew install rar  # macOS
sudo apt install rar  # Linux
```

## Development

```bash
# Install pre-commit hooks
pip install pre-commit
pre-commit install
```

## License

MIT
