# Versioning dan proses rilis

Jarvis memakai Semantic Versioning (`MAJOR.MINOR.PATCH`) sebagai kebijakan rilis. Dokumen ini menetapkan proses; keberadaannya tidak berarti tag, APK, atau release baru sudah diterbitkan. Persetujuan tampilan/finalisasi produk tidak menggantikan pengujian build, perangkat, dan deployment.

## Versi komponen saat dokumentasi ini ditulis

| Komponen | Sumber otoritatif | Nilai |
| --- | --- | --- |
| Android | `android/app/build.gradle.kts` | `versionName = "1.0.0"`, `versionCode = 1` |
| Varian internal | Gradle `versionNameSuffix` | `1.0.0-internal` |
| Paket Python server | `server/pyproject.toml` | `0.1.0` |
| FastAPI/health server | `server/src/jarvis_server/main.py` | `0.1.0` |
| Protokol WebSocket | hello klien/server dan `docs/PROTOCOL.md` | `1.0` |

Angka ini adalah metadata source, bukan bukti APK terpasang atau server aktif memakai revisi yang sama. Versi Android dan server sekarang berbeda; jangan menyebut server `1.0.0` hanya karena Android memakai nilai tersebut. Protokol memiliki versi terpisah. Catat commit untuk membedakan revisi yang masih memakai versionName sama.

## Aturan kenaikan versi

- **MAJOR**: perubahan tidak kompatibel, misalnya kontrak autentikasi/protokol yang menuntut migrasi klien/server. Jelaskan dampak, jalur upgrade dan rollback.
- **MINOR**: fitur kompatibel baru, misalnya perintah ekspresi baru yang tidak mengubah kontrak lama.
- **PATCH**: perbaikan kompatibel, misalnya clipping, timer UI, atau kesalahan API.
- Versi `0.x` masih tahap pengembangan; dokumentasikan setiap perubahan yang merusak kompatibilitas secara eksplisit.
- Prarilis memakai suffix seperti `1.1.0-rc.1`; build internal/debug bukan release produksi.
- Setiap APK baru yang akan didistribusikan harus memakai **versionCode yang lebih tinggi** daripada APK sebelumnya yang telah didistribusikan. `versionName` saja tidak menentukan urutan update Android. Catat kode terakhir dalam release notes. Jangan memakai ulang kode lama untuk rilis lain.

## Tag dan identitas rilis

Gunakan tag komponen agar tidak ambigu dalam monorepo:

- `android-v<MAJOR.MINOR.PATCH>` untuk APK Android.
- `server-v<MAJOR.MINOR.PATCH>` untuk gateway Python.
- Jika ingin release gabungan, gunakan `v<MAJOR.MINOR.PATCH>` hanya dengan manifest yang menyebut versi Android, versionCode, server, protokol, Hermes, dan commit secara eksplisit.

Ini konvensi untuk rilis berikutnya, bukan klaim bahwa tag tersebut sudah ada. Jangan memindahkan tag yang telah dipublikasikan; buat versi baru. `main` dapat berisi perubahan yang belum dirilis. Catat di bagian **Unreleased** pada [CHANGELOG](../CHANGELOG.md) sampai release benar-benar diverifikasi.

## Checklist sebelum rilis

1. Tentukan scope komponen dan commit, cek working tree, pisahkan eksperimen/pekerjaan yang belum tuntas. Jangan memasukkan token atau konfigurasi privat.
2. Perbarui versionName/versionCode Android atau versi paket server sesuai scope. Jika server berubah, sinkronkan pyproject dan versi FastAPI/health. Jangan menaikkan versi protokol kecuali kontraknya benar-benar berubah; sertakan rencana migrasi.
3. Isi changelog: Added/Changed/Fixed/Security, kompatibilitas, keterbatasan, dan catatan upgrade. Pindahkan entri Unreleased hanya setelah siap dirilis, dengan tanggal release aktual.
4. Jalankan tes Python, pemeriksaan XML/source dan diff. Build Android hanya pada mesin yang diizinkan. Verifikasi hasil build/unit test/lint, bukan hanya source check. Workflow CI saat ini manual; jangan menyatakan CI lulus jika belum dijalankan.
5. Uji di perangkat: auth, izin, microphone STOP, playback, reconnect, clipboard, timer 15 detik, ekspresi/perintah. Uji panggilan Hermes nyata pada akun service. Catat versi Hermes serta dukungan flag bridge yang dijelaskan di [INSTALLATION](INSTALLATION.md).
6. Siapkan APK bertanda tangan produksi dengan signing yang kompatibel. Jangan mengganti dengan debug signing atau menghapus data perangkat tanpa persetujuan. Jaga keystore di luar Git dan evaluasi exposure historis sesuai [BUILDING](BUILDING.md).
7. Buat tag dari commit yang telah diuji; jangan otomatis menandai `HEAD` yang belum diverifikasi. Publikasikan release notes beserta APK, SHA-256, versionCode, commit, versi server/protokol/Hermes dan matriks pengujian.
8. Koordinasikan deployment backend dan APK. Restart memutus percakapan aktif. Verifikasi health, penolakan auth tanpa token, handshake valid, lalu percakapan end-to-end secara terpisah.

## Manifest release yang disarankan

```text
Release: <tag>
Commit: <full SHA>
Android: <versionName> (versionCode <integer>)
APK: <filename> / SHA-256 <hash aktual>
Server: <package version>
Protocol: <hello version>
Hermes: <output hermes --version>
Tested: <perintah dan hasil nyata; perangkat/OS; tanggal>
Migration: <required steps or none>
Known limitations: <items>
```

Nilai placeholder di atas adalah template, bukan artifact atau hasil uji.

## Upgrade dan rollback

Simpan konfigurasi, registry hash-only, dan backup privat di luar repository. Simpan APK bertanda tangan yang diketahui berfungsi dan commit server sebelumnya. Android dapat menolak downgrade versionCode; pilih perbaikan dengan code lebih tinggi atau prosedur pemulihan yang disetujui, bukan memaksa uninstall. Rollback server harus tetap kompatibel dengan protokol dan autentikasi klien. Jangan mengembalikan endpoint publik menjadi tanpa auth untuk mengatasi kegagalan upgrade.
