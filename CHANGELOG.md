# Changelog

All notable changes are documented here, in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. This project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-11-08

### Added
- Show current PR files named in each failed job excerpt, with paginated reads and explicit gaps when files cannot be checked.

## [0.1.1] - 2026-11-01

### Fixed
- Read all job pages for large matrix runs and baseline comparisons, up to 10 pages; report incomplete reads instead of treating them as complete.

## [0.1.0] - 2026-11-01

### Added
- Failed job, step and log excerpt for a run, chosen by URL, ID, PR, branch or the default branch.
- Leads from default branch history and other runs of the same commit, and commits since the last pass.
- Text, Markdown and JSON output.
- `triaging-ci-failures` skill and plugin manifest.
