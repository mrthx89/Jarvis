# Petunjuk Penyesuaian google-services.json untuk Nexo

Dokumen ini berisi panduan resmi penyesuaian file konfigurasi klien Firebase (`google-services.json`) sehubungan dengan migrasi dan rebranding aplikasi Android dari paket `id.my.yhdev.jarvis` ke `id.my.yhdev.nexo`.

---

## 1. Ringkasan Perubahan

| Parameter | Versi Lama (Jarvis) | Versi Baru (Nexo) |
|---|---|---|
| **Application ID / Package** | `id.my.yhdev.jarvis` | `id.my.yhdev.nexo` |
| **Project Name (Gradle)** | `Jarvis` | `Nexo` |
| **Default WSS Endpoint** | `wss://jarvis.yhdev.my.id/ws/appliance` | `wss://nexo.yhdev.my.id/ws/appliance` |
| **Lokasi File Target** | `android/app/google-services.json` | `android/app/google-services.json` |

---

## 2. Langkah di Firebase Console

### Opsi A: Proyek Firebase yang Sudah Ada (Rekomendasi / Transisi Bertahap)
Jika Anda menggunakan proyek Firebase yang sama:
1. Buka [Firebase Console](https://console.firebase.google.com/) dan pilih proyek Anda.
2. Klik ikon gerigi (Project Settings / Setelan Proyek) di pojok kiri atas.
3. Di tab **General** (Umum), scroll ke bawah ke bagian **Your apps** (Aplikasi Anda).
4. Klik tombol **Add app** (Tambahkan aplikasi) lalu pilih ikon **Android**.
5. Masukkan data aplikasi:
   - **Android package name**: `id.my.yhdev.nexo`
   - **App nickname**: `Nexo Appliance` (opsional)
   - **Debug signing certificate SHA-1**: Masukkan jika menggunakan fitur Firebase Auth/Dynamic Links (opsional).
6. Klik **Register app**.
7. Unduh file `google-services.json` yang baru dihasilkan.
   > **Catatan**: Firebase akan menyertakan **seluruh** entri aplikasi Android yang terdaftar dalam proyek tersebut ke dalam satu file `google-services.json`. Dengan demikian, file tersebut secara otomatis berisi konfigurasi untuk `id.my.yhdev.nexo` dan `id.my.yhdev.jarvis` (dual-package compatibility).

### Opsi B: Proyek Firebase Baru (Nexo Mandiri)
1. Buat proyek baru di [Firebase Console](https://console.firebase.google.com/) dengan nama misalnya `nexo-appliance`.
2. Daftarkan aplikasi Android dengan package `id.my.yhdev.nexo`.
3. Unduh file `google-services.json`.

---

## 3. Penempatan File di Proyek

Salin file `google-services.json` yang diunduh ke lokasi:
```
android/app/google-services.json
```

Pastikan struktur JSON memiliki minimal blok client untuk `id.my.yhdev.nexo`:
```json
{
  "project_info": { ... },
  "client": [
    {
      "client_info": {
        "mobilesdk_app_id": "...",
        "android_client_info": {
          "package_name": "id.my.yhdev.nexo"
        }
      },
      ...
    }
  ],
  "configuration_version": "1"
}
```
*Contoh template dapat dilihat pada `android/app/google-services.json.example`.*

> **Penting**: File `android/app/google-services.json` bersifat privat dan diabaikan oleh `.gitignore` untuk mencegah kebocoran konfigurasi klien ke repository publik. Jangan menghapus aturan `.gitignore` untuk file ini.

---

## 4. Konfigurasi Remote Config di Firebase Console

Untuk mengaktifkan dynamic endpoint switching via Firebase Remote Config:
1. Pada Firebase Console, buka menu **Run > Remote Config** (atau **Build > Remote Config**).
2. Tambahkan atau sesuaikan parameter:
   - **Parameter key**: `server_websocket_url`
   - **Data type**: `String`
   - **Default value**: `wss://nexo.yhdev.my.id/ws/appliance`
3. Klik **Save** lalu **Publish changes**.

Aplikasi Nexo Android mendengarkan pembaruan Remote Config secara realtime melalui `ConfigUpdateListener` dan secara otomatis mengalihkan koneksi WebSocket tanpa restart aplikasi.
