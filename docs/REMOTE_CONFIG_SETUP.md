# Panduan Konfigurasi Firebase Remote Config
## Project: Jarvis Dedicated Appliance
**Tujuan**: Mengubah alamat IP / URL server WebSocket Jarvis secara dinamis dari cloud tanpa perlu kompilasi ulang (re-build) atau instalasi ulang file APK pada monitor/tablet.

---

### 1. Keunggulan Integrasi Remote Config

- **Zero-Touch Maintenance**: Jika IP server lokal Anda berubah (misalnya DHCP router berganti IP), atau Anda ingin mengalihkan koneksi dari jaringan lokal (LAN) ke alamat domain publik / Cloudflare tunnel, Anda cukup mengubah 1 parameter di Firebase Console.
- **Real-Time Hot Reload**: Klien Android telah dilengkapi `addOnConfigUpdateListener`. Begitu Anda menekan tombol *Publish* di Firebase Console, aplikasi di tablet/monitor langsung mendeteksi perubahan tersebut dan beralih ke server baru dalam hitungan detik tanpa perlu restart aplikasi.
- **Graceful Fallback**: Jika perangkat offline atau file konfigurasi Firebase belum dipasang, aplikasi otomatis menggunakan nilai cadangan lokal di `strings.xml` (`default_server_url`). Tidak ada risiko crash.

---

### 2. Langkah Pengaturan di Firebase Console

1. **Buat / Buka Proyek Firebase**:
   - Kunjungi [Firebase Console](https://console.firebase.google.com/).
   - Buat proyek baru atau pilih proyek yang sudah ada.

2. **Daftarkan Aplikasi Android**:
   - Tambahkan aplikasi Android dengan Package Name:
     `com.yhdev.jarvis`
   - Unduh file `google-services.json`.
   - Letakkan file tersebut di dalam folder repositori:
     `android/app/google-services.json`

3. **Buat Parameter di Remote Config**:
   - Di menu sebelah kiri Firebase Console, pilih **Run > Remote Config** (atau **Engage > Remote Config**).
   - Klik **Create configuration** atau **Add parameter**.
   - Isi formulir parameter sebagai berikut:
     * **Parameter name**: `server_websocket_url`
     * **Data type**: `String`
     * **Default value**: Masukkan alamat WebSocket server Anda, contoh:
       - Mode Lokal LAN: `ws://192.168.1.100:8765/ws/appliance`
       - Mode Domain / Cloudflare: `wss://jarvis.domainanda.com/ws/appliance`
       - Mode IP Publik: `ws://44.255.96.143:8765/ws/appliance`
   - Klik **Save**, kemudian klik **Publish changes**.

---

### 3. Pengujian Perubahan URL Real-Time

1. Jalankan aplikasi Jarvis di tablet / Android.
2. Buka Firebase Console > Remote Config.
3. Ubah nilai `server_websocket_url` ke alamat baru lalu klik **Publish**.
4. Perhatikan layar monitor Android Anda: status akan menampilkan *"Mengalihkan ke server baru..."* dan langsung terhubung ke alamat baru secara instan.
