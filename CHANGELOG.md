# Changelog

Perubahan produk dicatat di sini. Format bagian mengikuti Added / Changed / Fixed / Security dan kebijakan [Semantic Versioning proyek](docs/VERSIONING.md). Entri **Unreleased** adalah perubahan source, bukan pengumuman APK atau layanan yang telah diperbarui.

## Unreleased

### Added

- Perintah ekspresi singkat berbahasa Indonesia yang dikenali gateway, seperti “Jarvis tersenyum”, “coba tertawa”, dan “kembali normal”, dengan acknowledgement tetap tanpa panggilan penalaran Hermes.
- Pemilihan ekspresi percakapan berdasarkan frasa konteks terbatas, dengan fallback netral.
- Penahanan ekspresi singkat setelah playback selesai dan perlindungan agar event TTS tidak menimpa ekspresi yang dipilih.
- README dengan identitas maskot, screenshot pemakaian, integrasi Hermes Agent, tautan dukungan pengembangan, serta panduan instalasi/versioning.

### Changed

- Mendengarkan memakai ekspresi dasar netral, bukan selalu terkejut.
- Dokumentasi membedakan metadata versi source, artifact rilis, proses server aktif, dan verifikasi perangkat.

### Verification / deployment

- Tidak ada build Android/remote build CI yang dijalankan di server untuk iterasi ini.
- Tes parser/pipeline Python dan source guards Android tidak membuktikan kompilasi Kotlin maupun perilaku perangkat.
- Perubahan backend memerlukan restart/deployment terkoordinasi; source pull saja tidak mengaktifkannya.
- Versi metadata saat ini: Android `1.0.0` (code `1`), server `0.1.0`, protokol `1.0`. Tidak ada bump/tag/release baru yang dibuat hanya dengan penambahan changelog ini.

## Riwayat source sebelum changelog

Ringkasan ini berasal dari commit yang ada, bukan tanggal release atau jaminan artifact:

| Commit | Perubahan |
| --- | --- |
| `4236166` | Ikon clipboard pada header, caption ON 15 detik, panel tersembunyi saat OFF. |
| `459c0d3` | Reaksi menangis/optimis ketika kontrol suara berubah. |
| `9e8fa2c` | Menghapus penggunaan `errorMaxLines` yang tidak tersedia di Material 1.11.0. |
| `3963da9` | Bottom sheet responsif, penyalinan jawaban final lengkap, notch kecil, dan kontrol suara eksplisit. |

Lihat `git log` untuk riwayat penuh. Release berikutnya harus mengisi tanggal, tag, commit, versionCode, artifact checksum, hasil uji aktual dan kebutuhan migrasi sesuai [VERSIONING](docs/VERSIONING.md).
