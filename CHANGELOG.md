# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- PDF to CBR conversion support using PyMuPDF
- New `--dpi` option for PDF rendering quality (default: 200)
- PyMuPDF dependency for PDF page extraction

### Changed
- Updated CLI to accept both EPUB and PDF files
- Banner and documentation updated to reflect EPUB/PDF support
### Added

## [v0.1.0] - 2025-12-06

### Changed
- fix: CSS scaling for InDesign-exported EPUBs [MINOR] (#3) (aa1197d)
- docs: Update CHANGELOG.md for v0.0.5 [skip ci] (5992a3c)

## [v0.0.5] - 2025-12-06

### Changed



## [v0.0.1] - 2025-12-05

### Changed
- feat: Prepare for first release (#1) (477dc46)
- feat: Add epub2cbr tool for converting EPUB comic/manga files to CBR (9198834)


- Initial release of epub2cbr converter
- Support for three EPUB types:
  - Text embedded in images → Direct extraction (best quality)
  - Single/Split images without text → Screenshot with gowitness
  - Split images with text → Image concatenation
- Automatic EPUB type detection
- CBR creation with RAR (x64) or CBZ with ZIP (ARM fallback)
- Parallel processing with gowitness batch mode
- Docker support with multi-architecture images (amd64/arm64)
- GitHub Actions CI/CD pipeline:
  - Lint and syntax checking (flake8, pylint)
  - Docker build and test on PRs
  - Automatic releases with semantic versioning
  - Smart version bumping with `[MAJOR]`/`[MINOR]` commit markers
- Pre-commit hooks for code quality
- Renovate configuration for dependency updates
- mise.toml for local development setup

### Features
- `--mode auto|extract|screenshot` - Choose conversion method
- `--cbr` / `--cbr-only` - Create CBR/CBZ archive
- `--jpeg-quality` - Control JPEG compression quality
- `--threads` - Parallel screenshot processing
- `--timeout` / `--delay` - Screenshot timing control

[Unreleased]: https://github.com/romancin/epub2cbr/compare/v0.1.0...HEAD
[v0.1.0]: https://github.com/romancin/epub2cbr/releases/tag/v0.1.0
[v0.0.5]: https://github.com/romancin/epub2cbr/releases/tag/v0.0.5
[v0.0.1]: https://github.com/romancin/epub2cbr/releases/tag/v0.0.1
