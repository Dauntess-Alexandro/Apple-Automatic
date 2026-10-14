# Build Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** One-click Flutter iOS build and App Store Connect upload in Apple Automatic.

**Architecture:** A dedicated service module owns identity checks, encrypted GitHub configuration, managed workflow generation, dispatch and monitoring. A dedicated Qt panel consumes the existing API fields through a callback; the main window only embeds it and handles shutdown.

**Tech Stack:** Python, requests, PyJWT/cryptography, PyNaCl, PySide6, GitHub REST API 2026-03-10, GitHub Actions.

**Spec:** docs/superpowers/specs/2026-10-12-build-upload.md

## Global Constraints

- Reuse current Apple credential fields; never duplicate or persist their values in tab preferences.
- Support Flutter iOS projects, including app roots nested in the repository.
- Verify bundle/app identity before secret mutations; preserve an existing certificate secret.
- Never implicitly publish local source edits, replace unrelated workflows, or submit for App Review.
- Preserve unrelated dirty workspace changes; no bulk staging or commits.
- UI stays responsive; close/resume observation must preserve the cloud run.

## Review Focus

- Wrong Apple app / multiple Flutter apps: fail before any secret mutation.
- Token and .p8 leakage: no plaintext secrets in settings, workflows, or error output.
- Concurrent builds: workflow concurrency and exact dispatch run ID.
- Interrupted observation: persist run identity and allow resuming.
- Existing workflow or certificate: never overwrite unknown workflow or rotate existing signing secret.

### Task 1: Build service

- [ ] Add failing tests for identity validation, nested app discovery, workflow safety, encrypted secrets, and dispatch/result handling. Run tests and observe failure because the service is absent.
- [ ] Implement github_build_upload.py, pinned template, authentication, config and service. Add PyNaCl dependency. Run tests; expected all service tests pass.

### Task 2: Qt integration

- [ ] Add failing tests for reading shared credentials, per-app preferences, worker lifecycle and failure/success UI. Run and observe failures.
- [ ] Implement build_upload_panel.py, embed the tab, hide unrelated global action, handle shutdown. Run focused UI tests; expected all pass.

### Task 3: Verification and delivery

- [ ] Validate generated YAML with actionlint, run relevant existing tests plus the full unit suite where practical.
- [ ] Inspect real GUI and verify Carelink through read-only API checks, then a real managed workflow if practical.
- [ ] Document usage and rebuild the desktop application if closed; preserve the running application otherwise.
- [ ] Review complete diff, fix material issues, and deliver precise results.
