# epub2cbr

[![CI](https://github.com/romancin/epub2cbr/actions/workflows/ci.yml/badge.svg)](https://github.com/romancin/epub2cbr/actions/workflows/ci.yml)

Convert EPUB comic/manga files to CBR (Comic Book RAR) archives.

## Features

- **Automatic detection** of EPUB structure for optimal conversion
- **High-quality output** with automatic resolution scaling
- **CBR creation** ready for comic book readers
- Preserves reading order from EPUB metadata

## Quick Start

```bash
# Using Docker (recommended - no dependencies needed)
docker run --rm -v $(pwd):/data romancin/epub2cbr comic.epub --cbr-only

# Or install locally with mise
mise install && mise run setup-all
./epub2cbr.py comic.epub --cbr-only
```

## Installation

### Using Docker (recommended)

No dependencies needed - everything is included in the container.

```bash
# Build the image
docker build -t epub2cbr .

# Convert a single file
docker run --rm -v $(pwd):/data epub2cbr manga.epub --cbr-only

# Batch conversion
for f in *.epub; do docker run --rm -v $(pwd):/data epub2cbr "$f" --cbr-only; done
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
# Basic conversion (auto-detects best method)
./epub2cbr.py comic.epub --cbr-only

# Batch conversion
for f in *.epub; do ./epub2cbr.py "$f" --cbr-only; done

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
| `-m extract` | Force direct image extraction |
| `-m screenshot` | Force screenshot mode (gowitness) |
| `--threads N` | Parallel threads for screenshot mode (default: 4) |
| `--keep-extracted` | Keep extracted EPUB files |
| `--timeout N` | Screenshot timeout in seconds |
| `--check-deps` | Verify all dependencies |

## How it works

The tool analyzes EPUB structure to choose the best conversion method:

| EPUB Type | Detection | Method |
|-----------|-----------|--------|
| Text embedded in images | Empty `TextContainer` | Direct extraction |
| Text in HTML overlay | `TextContainer` has content | Screenshot rendering |

## Requirements

| Dependency | Required | Purpose |
|------------|----------|---------|
| Python 3 | Yes | Core runtime |
| Pillow | Yes | Image processing |
| gowitness | Only for HTML text | Screenshot capture |
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
