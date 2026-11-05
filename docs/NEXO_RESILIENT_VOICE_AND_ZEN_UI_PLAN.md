# Rencana Perbaikan: Resilient Continuous Voice & Zen Smart UI Nexo

**Tanggal:** 4 November 2026  
**Target:** Nexo Android Client & Backend Appliance Integration  
**Kreator / Pemilik:** {YH Dev}  
**Co-Pilot:** Nexus (berkarya dengan hati @ 2026)  

---

## 1. Latar Belakang & Identifikasi Masalah

Pengguna mengidentifikasi dua kendala utama dalam pengalaman penggunaan sehari-hari:
1. **Mikrofon Nexo Tiba-tiba Mati (OFF):** Saat mikrofon sudah dinyalakan (`Mulai mikrofon`), beberapa saat kemudian atau setelah bicara/terjadi jeda, mikrofon tiba-tiba mati sendiri menjadi status `OFF` (tombol kembali ke `Mulai mikrofon`, status berubah menjadi `Periksa koneksi / akses`, dan Nexo menampilkan ekspresi menangis).
2. **UI Masih Terlalu Rumit / Banyak Elemen Kaku:** Tampilan aplikasi masih memuat artefak teknis (seperti kotak konsol `NEXO // COGNITIVE STREAM`, tombol clipboard copy kecil, teks mood yang bertumpuk dengan subtitle, serta dua baris pill tebal di atas) yang tidak mencerminkan karakteristik *smart app* modern.

---

## 2. Analisis Akar Masalah (Root Cause Analysis)

### Masalah A: Mengapa Mikrofon Tiba-tiba OFF?
Berdasarkan audit mendalam pada `NexoKioskService.kt`, `AudioOwnership.java`, dan `NexoWebSocketClient.kt`:
1. **Timeout Negosiasi `conversation_resume` Terlalu Sempit (3.0 Detik):**
   - Pada `AudioOwnership.java`, pemanggilan `resume()` menetapkan batas waktu kaku: `deadline = now + 3000` (3 detik).
   - Jika koneksi seluler/outdoor mengalami latensi handshake >3 detik, `ownership.expired()` bernilai `true`.
   - `tick` langsung memanggil `fail("Lease atau konfirmasi mikrofon kedaluwarsa")` yang mengeksekusi `pauseLocal("error")` $\rightarrow$ `persistOff()`.
2. **Cascading Kill-Switch pada `fail()`:**
   - Semua kegagalan minor (seperti `upload_timeout`, `processing_timeout`, koneksi sesaat terputus, atau audio glitch) bermuara pada `fail()`.
   - `fail()` mematikan status `voiceEnabled`, memutuskan WebSocket, dan mematikan audio recorder.
   - Walaupun ada upaya `keepVoiceAlive` dan `scheduleAutoRestart()`, jeda pemulihan (1.2–2.5 detik) menyebabkan tombol di layar sempat berganti menjadi `OFF`, ekspresi Nexo berganti menangis, dan status LED menjadi merah.
3. **Transient Disconnect Mereset State Mikrofon:**
   - Ketika WebSocket sempat *ping timeout* atau terputus sekejap (misal perpindahan BTS seluler), `transientDisconnect()` langsung memanggil `persistOff()` dan menghentikan audio loop lokal.

### Masalah B: Mengapa UI Belum Terasa "Zen Smart App"?
1. **Kotak Konsol Kaku (`consoleCard`):** Menggunakan latar belakang kaca tebal dengan header teks monospace kapital (`NEXO // COGNITIVE STREAM`) dan tombol icon clipboard kecil yang menambah kepadatan visual.
2. **Teks Ganda Bertumpuk:** `voiceMood` ("Suara aktif") diletakkan tepat di atas `consoleCard` yang juga menampilkan `tvSubtitle` (transkripsi), sehingga mata pengguna terdistraksi oleh dua teks berbeda di bawah maskot.
3. **Pill Status Atas:** Dua pill terpisah dengan border tebal `bg_hud_control` di kiri dan kanan atas mengambil ruang vertikal yang seharusnya memberi kelegaan visual bagi avatar Nexo.

---

## 3. Strategi Solusi Menyeluruh

### Pilar 1: "Continuous Resilient Voice" (Anti-Mati Sendiri)
Prinsip dasar: **User Intent adalah Raja. Sekali user menekan `Mulai mikrofon`, mikrofon tetap AKTIF sampai user sendiri yang menekan `Matikan mikrofon`.**

1. **State Protection (`isUserIntentActive`):**
   - Variabel intensi pengguna (`userVoiceIntent`) tidak boleh diubah oleh error jaringan, timeout server, atau paket hilang.
   - Hanya fungsi `stopVoice()` (dari klik manual tombol toggle) atau penutupan aplikasi yang boleh mereset intensi ini.
2. **Graceful Background Recovery Tanpa Mengubah UI ke OFF:**
   - Jika koneksi internet terputus sesaat, UI **tetap dalam keadaan ON (Menyimak / Menghubungkan ulang)**, bukan berubah menjadi OFF / menangis.
   - Audio recorder lokal tetap berjalan atau langsung *re-arm* otomatis begitu jalur data pulih tanpa memerlukan tindakan ulang dari pengguna.
3. **Relaksasi Timeout & Deadline Negosiasi:**
   - Memperlebar batas waktu `resumeRequest` di `AudioOwnership.java` dari 3.0 detik menjadi 15.0 detik untuk mengakomodasi jitter jaringan outdoor.
   - Memastikan respons error dari server (seperti `stt_empty`, `processing_failed`, atau `timeout`) tidak memanggil `fail()` yang mematikan mikrofon, melainkan mengalir mulus ke `maybeNextTurn()` untuk terus mendengarkan kalimat berikutnya.

### Pilar 2: "Zen Smart Appliance UI" (Desain Minimalis & Modern)
Prinsip dasar: **Anti-Slop, tanpa dekorasi berlebih, fokus pada interaksi alami pengguna dan maskot.**

1. **Clean Floating Typography untuk Percakapan:**
   - Menghilangkan bingkai kaku `consoleCard` dan header `NEXO // COGNITIVE STREAM`.
   - Menghadirkan teks percakapan melayang (*clean floating typography*) di bawah avatar Nexo: font terbaca jelas, kontras tinggi di atas latar gelap, transisi mulus.
   - Tombol salin (`copyReply`) diintegrasikan secara elegan dan halus di samping teks tanpa kotak pembungkus kaku.
2. **Penyatuan Status Atas yang Elegan:**
   - Mengurangi ketebalan border pill atas menjadi indikator status minimalis yang bersih (LED dot + teks ringkas).
3. **Satu Tombol Aksi Jempol (One Thumb Action):**
   - Hanya ada 1 tombol utama yang besar, mudah ditekan dengan satu tangan:
     - State Idle: **`Mulai Mikrofon`** (Aksen Cyan / Glowing).
     - State Aktif: **`Matikan Mikrofon`** (Aksen Gelap Elegan dengan indikator aktif menyimak).
   - Seluruh kontrol manual/sakelar tersembunyi sepenuhnya dari pengalaman pengguna normal.

---

## 4. Rincian Rencana Eksekusi (Action Items)

### Fase 1: Hardening Sisi Android Service & Audio Ownership
- **File:** `android/app/src/main/java/id/my/yhdev/nexo/audio/core/AudioOwnership.java`
  - Perlebar deadline negosiasi `resume()` dan `acquire()` dari 3.0s ke 15.0s.
  - Cegah pembatalan mendadak pada `normalAllowed()` saat terjadi *transient socket reset*.
- **File:** `android/app/src/main/java/id/my/yhdev/nexo/service/NexoKioskService.kt`
  - Kuatkan invariant `userVoiceIntent`: jangan biarkan `fail()` mereset `voiceEnabled` ke `false` jika disebabkan oleh transient failure.
  - Sempurnakan transisi `maybeNextTurn()` agar langsung membuka rekaman Silero VAD berikutnya secara kontinu setelah TTS atau kegagalan minor selesai.
  - Perbaiki `transientDisconnect()` agar langsung menyambung ulang WebSocket di latar belakang tanpa mengubah status tombol utama menjadi OFF.

### Fase 2: Redesain & Penyederhanaan Tata Letak UI
- **File:** `android/app/src/main/res/layout/activity_main.xml`
  - Hapus elemen kaku `consoleCard`, ganti dengan layout subtitle percakapan melayang (`conversationArea` + `tvSubtitle` + `copyReply` minimalis).
  - Hilangkan redundansi `voiceMood` yang bertumpuk dengan subtitle.
  - Perhalus styling `pillStatus` dan `pillVoiceprint` agar terasa ringan dan menyatu dengan status bar.
- **File:** `android/app/src/main/java/id/my/yhdev/nexo/MainActivity.kt`
  - Sesuaikan binding UI dengan layout baru tanpa merusak kontrak data assistant reply.
  - Pastikan tombol utama `toggleVoice` merespons secara konsisten dan instan.

### Fase 3: Pengujian & Validasi Statis Regresi
- **File:** `server/tests/test_android_mobile_layout_static.py`
  - Sesuaikan asersi layout dengan struktur UI Zen yang baru (tetap memverifikasi touch target `>= 48dp`, ketiadaan ScrollView bersarang, dan ketersediaan fungsionalitas copy).
- **File:** `server/tests/test_android_outdoor_service_static.py`, `server/tests/test_android_voice_control_static.py`, dll.
  - Pastikan seluruh 839+ host test suite di Python pytest tetap lulus 100%.

### Fase 4: Build, Publish Server (jika diperlukan) & Uji Coba Pengguna
- Verifikasi integritas git diff (`git diff --check`).
- Push perubahan ke branch `main` GitHub agar siap di-pull oleh pengguna di Android Studio.

---

## 5. Indikator Keberhasilan (Acceptance Criteria)

1. **Stabilitas Mikrofon:**
   - Mikrofon yang dinyalakan **tidak pernah mati sendiri** akibat hening, suara angin, latensi jaringan, atau transkripsi kosong.
   - Status tombol tetap menunjukkan `Matikan Mikrofon` (aktif) hingga pengguna sengaja mengetuknya.
2. **Kebersihan Antarmuka:**
   - Tidak ada lagi kotak border konsol atau teks teknis kaku.
   - Maskot Nexo tampil lapang di tengah layar dengan subtitle percakapan yang bersih dan nyaman dibaca.
3. **Kualitas Kode:**
   - Seluruh test suite (839+ tests) lulus hijau tanpa regresi fungsional atau pelanggaran keamanan.
