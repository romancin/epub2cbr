# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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

[Unreleased]: https://github.com/romancin/epub2cbr/commits/main
