# AGENTS.md — Nexo Dedicated Appliance

## Project Identity & Vision
- **Project**: Nexo Dedicated Appliance
- **Architect & Lead**: Yanto Hariyono (YH Dev)
- **Ethos**: "Berkarya dengan hati @ 2026" — menggabungkan stabilitas, kejelasan logika rekayasa, dan nilai guna humanis.
- **Objective**: Menghadirkan asisten AI otonom (Nexo) dalam bentuk physical smart appliance (monitor + mikrofon/speaker) tanpa ketergantungan pada PC konvensional di meja kerja.

## Core Architectural Invariants
1. **Decoupled Architecture**: Klien fisik (Android Smart Screen/Tablet) hanya bertindak sebagai "Indra dan Wajah" (Audio Capture, Audio Playback, Reactive Visualizer). Seluruh pemrosesan kognitif, LLM/Hermes, STT, dan TTS berada di Server.
2. **Native Over WebView**: Klien Android dibangun dengan Native Kotlin. Menghindari WebView demi stabilitas 24/7 tanpa memory leak, kontrol latensi audio rendah (low-latency PCM), dan pemanfaatan hardware Acoustic Echo Cancellation (AEC).
3. **Full-Duplex Streaming**: Komunikasi audio dan status visual menggunakan WebSocket biner dan JSON frames secara bersamaan.
4. **Resilience & Zero Touch**: Klien Android wajib mampu memulihkan koneksi secara mandiri (exponential backoff reconnect), auto-start saat boot, dan menjaga layar tetap aktif tanpa campur tangan pengguna.

## Repository Conventions
- `android/`: Native Android Application (Kotlin, Gradle Kotlin DSL, Min SDK 26, Target SDK 34).
- `server/`: Python Async Gateway (FastAPI, WebSockets, Hermes Agent Bridge, STT/TTS Pipelines).
- `docs/`: Spesifikasi formal (PRD, Arsitektur, Protokol Komunikasi, Panduan Perangkat Keras).

## Branch & Commit Standards
- Commit format: Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`).
- Author identity: `mrthx89 <mrthx.89@gmail.com>`.
