# Pembersihan history kredensial — 2026-11-01

History branch `main` ditulis ulang untuk menghapus file Google/Firebase asli dan keystore yang pernah dikomit. Remote saat operasi hanya memiliki satu branch (`main`) dan tidak memiliki tag yang diiklankan. Semua versi blob yang ditemukan untuk file berikut dihapus dari history yang dapat dijangkau:

- `android/app/google-services.json`
- `android/Nexo.jks`
- `android/Nexo` — salinan keystore historis tanpa ekstensi, ditemukan lewat identitas blob.

Lima versi blob sensitif tidak lagi dapat dijangkau dari refs yang diverifikasi. Tree source terbaru sebelum/sesudah rewrite identik. File fisik `android/Nexo.jks` dan `android/app/google-services.json` pada working directory server dipertahankan dan SHA-256-nya dibandingkan sebelum/sesudah tanpa mencetak isinya. File contoh `google-services.json.example` tetap tersedia.

## Clone yang sudah ada

SHA commit berubah. Jangan melakukan merge/push dari history lama karena dapat memasukkan kembali data yang dibersihkan. Cara paling aman:

1. Cadangkan file fisik privat (`google-services.json`, keystore, local.properties, config dan token) serta perubahan kerja yang belum dikomit ke lokasi aman di luar repository. Jangan memasukkan backup ini ke Git.
2. Simpan direktori clone lama tanpa menghapus file fisiknya; buat clone baru ke direktori lain dari remote yang sudah dibersihkan.
3. Salin hanya konfigurasi privat dan perubahan source yang diperlukan ke clone baru. Jangan menyalin folder `.git`, bundle history lama, atau menggabungkan branch lama.
4. Verifikasi ignore rules sebelum staging. Jangan gunakan `git add -f` untuk file rahasia. Rekan yang memiliki fork/clone perlu membersihkan salinannya juga.

Jangan menjalankan reset/clean pada working tree berisi perubahan atau kredensial tanpa backup. Pada server pengembang, ref lokal sudah diselaraskan tanpa checkout/reset, sehingga perubahan yang belum selesai dan file fisik tetap ada.

## Batas penghapusan

Force-push mengubah history branch, bukan pencabutan kredensial. Salinan dalam fork, clone lama, backup privat, pull-request refs, tampilan commit lama dan cache GitHub dapat bertahan. Untuk penghapusan server-side yang lebih menyeluruh, hubungi GitHub Support dengan data sensitif tetap dirahasiakan. Commit pertama yang terdampak sebelum rewrite: `4916b1e2789e1785e074170da52ee8262846596f`. Cadangan history lama sengaja disimpan privat di luar repository untuk pemulihan administratif; tidak boleh diunggah kembali.

`google-services.json` merupakan konfigurasi klien Firebase, bukan otomatis kunci administrator/service-account. Tetap evaluasi pembatasan API key dan aturan Firebase. Keystore yang pernah terekspos memerlukan evaluasi rotasi/reset upload key sesuai mekanisme distribusi dan signing; menghapus history tidak mengamankan salinan yang sudah diunduh. Tidak ada rotasi, penghapusan cache GitHub, atau perubahan signing yang dilakukan dalam operasi ini.
