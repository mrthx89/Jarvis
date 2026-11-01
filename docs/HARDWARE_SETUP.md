# Hardware Setup & Appliance Deployment Guide
## Project: Nexo Dedicated Appliance (formerly Jarvis)
**Target**: Hands-free Dedicated Desktop Experience (No PC)

---

### 1. Pilihan Konfigurasi Perangkat Keras

Terdapat tiga kombinasi perangkat keras yang direkomendasikan berdasarkan preferensi ruang meja:

#### Opsi 1: Tablet Android + Dudukan Meja Logam (Paling Rapi & Minimalis)
- **Perangkat**: Tablet Android 11–13 inci (RAM minimal 4GB, Android 10+).
- **Aksesoris**: Dudukan meja magnetik/logam dengan jalur kabel tersembunyi.
- **Kelebihan**: Layar, kamera/sensor cahaya, mikrofon ganda, dan speaker terintegrasi rapi dalam satu panel tipis.

#### Opsi 2: Smart Monitor + USB Conference Speakerphone (Layar Besar)
- **Perangkat**: Smart Monitor Android 24–32 inci (misal monitor berbasis Android TV/AOSP).
- **Audio Eksternal**: USB Conference Speakerphone (Anker PowerConf, Jabra Speak, atau ReSpeaker USB Mic Array).
- **Penempatan**: Monitor dipasang pada monitor arm; speakerphone diletakkan tepat di bawah monitor menghadap posisi duduk Anda.
- **Kelebihan**: Visual "wajah" Nexo tampil megah di meja kerja dan tangkapan suara 360 derajat sangat sensitif.

#### Opsi 3: Monitor Standar + Android TV Box di Belakang Layar
- **Perangkat**: Monitor PC biasa (HDMI) + Android TV Box / Streaming Stick yang dipasang di port belakang layar.
- **Audio**: USB Speakerphone terhubung ke port USB Android TV Box.

---

### 2. Panduan Akustik & Penempatan Mikrofon

1. **Jarak Mikrofon ke Pengguna**: Idealnya antara 60 cm hingga 150 cm dari posisi duduk.
2. **Pencegahan Resonansi Suara**:
   - Jika menggunakan speakerphone terpisah, letakkan di atas alas mousepad atau dudukan karet peredam agar getaran bass saat Nexo berbicara tidak merambat ke meja.
3. **Pemanfaatan Hardware AEC**:
   - Pastikan aplikasi mengaktifkan `AcousticEchoCanceler`. Dengan AEC aktif, Nexo dapat mendengar suara instruksi Anda meskipun saat itu Nexo sendiri sedang berbicara melalui speaker ("Barge-in capability").

---

### 3. Konfigurasi Sistem Operasi Android untuk Kiosk 24/7

Agar perangkat stabil berjalan terus-menerus tanpa gangguan:

1. **Developer Options**:
   - Masuk ke *Settings > System > Developer Options*.
   - Aktifkan **Stay Awake** (Layar tidak pernah tidur saat mengisi daya).
2. **Battery Protection (Perlindungan Baterai)**:
   - Jika menggunakan tablet dengan baterai, aktifkan fitur *Protect Battery* (membatasi pengisian hingga 80-85%) untuk menjaga keawetan sel baterai saat dicolokkan terus-menerus ke adaptor.
3. **Kiosk Lock Task Mode**:
   - Jadikan aplikasi Jarvis sebagai Device Owner (opsional untuk lingkungan produksi) atau gunakan fitur *App Pinning* / *Lock Task Mode* agar aplikasi tidak dapat keluar tanpa kata sandi.
4. **Izin Aplikasi (App Permissions)**:
   - Berikan izin `RECORD_AUDIO` secara permanen (*Allow all the time*).
   - Matikan *Battery Optimization* untuk aplikasi Jarvis (*Unrestricted*).
