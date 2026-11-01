# Panduan Kompilasi & Build Lokal APK Android (Local Developer Build Guide)
## Project: Nexo Dedicated Appliance
**Architect**: Yanto Hariyono (YH Dev)  
**Target Platform**: Android (Min SDK 26 / Android 8.0 Oreo, Target SDK 34 / Android 14.0)  
**Gradle Wrapper**: 8.7.0 | **JDK**: Java 17  

---

## ⚠️ PENEGASAN MUTLAK: LARANGAN BUILD DI SERVER

> **PERINGATAN KERAS / STRICT POLICY**:  
> **DILARANG KERAS menjalankan perintah `./gradlew`, `gradle`, Android Studio, atau melakukan kompilasi build APK Android pada lingkungan Server (VPS / Cloud Instance)!**

### Alasan Teknis Larangan Build di Server:
1. **Server Adalah Lingkungan Headless Produksi**:  
   Server VPS hanya dialokasikan untuk menjalankan gateway Python (`nexo_server`), inferensi suara (Faster-Whisper STT, Edge-TTS), dan jembatan penalaran Hermes Agent CLI. Server tidak memiliki desktop display environment, X11/Wayland, maupun GUI tools.
2. **Ketiadaan Android Toolchain & SDK**:  
   Server tidak dilengkapi dengan Android SDK Platform-Tools, Build-Tools, NDK, AAPT, maupun emulator Android.
3. **Pencegahan Kehabisan Memori (OOM Hazard)**:  
   Kompilasi Gradle daemon dan JVM compiler membutuhkan alokasi RAM yang sangat besar (2–4 GB+). Menjalankan Gradle di server berisiko tinggi memicu kernel *Out-of-Memory (OOM) Killer* yang dapat mematikan proses gateway `nexo-appliance` dan database Hermes.
4. **Keamanan Kredensial & Signing Key**:  
   File private keystore (`.jks`) untuk rilis produksi dan password signing **TIDAK BOLEH** disimpan di server VPS. Seluruh penandatanganan APK produksi wajib dilakukan secara terisolasi pada mesin lokal pemilik (*owner's secure local machine*).
5. **Verifikasi Server Dilakukan Secara Statis**:  
   Integritas kode Android (XML drawables, integritas sintaks Kotlin, resource tags, file earcon) pada sisi server diverifikasi menggunakan static analysis guards berbasis Python pytest (`server/tests/test_android_static_guards.py`) **tanpa pernah memanggil Gradle atau AAPT**.

---

## 1. Prasyarat Komputer Lokal Developer

Seluruh proses kompilasi dan pembuatan file APK **hanya dilakukan di komputer lokal developer** (Laptop / Workstation PC):

- **Sistem Operasi**: Windows 10/11, macOS (Apple Silicon / Intel), atau Linux Desktop (Ubuntu, Fedora, Arch).
- **IDE**: [Android Studio](https://developer.android.com/studio) versi terbaru (rekomendasi: Hedgehog 2023.1.1, Iguana, Jellyfish, Koala, atau Ladybug).
- **Java Development Kit (JDK)**: **JDK 17** (wajib). Gunakan OpenJDK 17 atau Azul Zulu JDK 17. Gradle 8.7 tidak kompatibel dengan JDK 21 atau JDK 8.
- **Android SDK**:
  - SDK Platform: API Level 34 (Android 14.0 UpsideDownCake).
  - SDK Build-Tools: 34.0.0.
  - Android SDK Platform-Tools (termasuk `adb`).
- **Git** & **ADB**: Terpasang dan dapat diakses dari terminal/command prompt.

---

## 2. Persiapan Proyek di Komputer Lokal

### Langkah 1: Kloning Repositori
Buka terminal pada komputer lokal Anda:
```bash
git clone https://github.com/mrthx89/Nexo.git
cd Nexo
```

### Langkah 2: Membuka Proyek di Android Studio
1. Buka Android Studio.
2. Pilih menu **File > Open...** (atau dari Welcome Screen: **Open**).
3. Arahkan ke sub-direktori `android/` di dalam repositori Nexo (bukan root repositori).
4. Klik **OK** dan biarkan Android Studio mendeteksi proyek Gradle.

### Langkah 3: Konfigurasi SDK & JDK di Android Studio
- Buka **Settings / Preferences > Build, Execution, Deployment > Build Tools > Gradle**.
- Pada opsi **Gradle JDK**, pastikan terpilih **JDK 17** (misal: *Embedded JDK 17* atau *jbr-17*).
- Jalankan Gradle Sync (**File > Sync Project with Gradle Files**).
- Gradle Wrapper v8.7.0 yang sudah dipin akan mengunduh dependensi secara otomatis.

### Langkah 4: Konfigurasi `google-services.json` (Firebase)
Aplikasi Nexo menggunakan Firebase Client untuk package `id.my.yhdev.nexo`.
- Dapatkan file `google-services.json` resmi dari konsol Firebase pemilik proyek (lihat [docs/FIREBASE_SETUP.md](FIREBASE_SETUP.md)).
- Tempatkan file tersebut pada path:
  ```text
  android/app/google-services.json
  ```
- *Catatan*: File ini berisi konfigurasi client Firebase, bukan private service account server.

### Langkah 5: SDK Path (`local.properties`)
Jika Android Studio belum otomatis membuatnya, buat file `android/local.properties` lokal:
- **Linux / macOS**:
  ```properties
  sdk.dir=/home/<username>/Android/Sdk
  # atau macOS: sdk.dir=/Users/<username>/Library/Android/sdk
  ```
- **Windows**:
  ```properties
  sdk.dir=C\:\\Users\\<username>\\AppData\\Local\\Android\\Sdk
  ```
*(Pastikan `local.properties` tidak dicommit ke Git - sudah ada di `.gitignore`)*.

---

## 3. Kompilasi & Build Varian APK Internal / Debug

Varian `internal` dirancang khusus untuk pengujian langsung oleh developer di perangkat tablet atau monitor kios. Varian ini menggunakan penandatanganan debug otomatis (*debug signing*) sehingga tidak memerlukan file keystore eksternal.

### Perintah Build Terminal Lokal:

- **Linux / macOS**:
  ```bash
  cd android
  ./gradlew testDebugUnitTest assembleInternal
  ```

- **Windows (Command Prompt / PowerShell)**:
  ```cmd
  cd android
  gradlew.bat testDebugUnitTest assembleInternal
  ```

### Lokasi Output APK Internal:
File APK yang dihasilkan akan berada di:
```text
android/app/build/outputs/apk/internal/app-internal.apk
```

### Cara Memasang ke Tablet Android via ADB:
1. Hubungkan tablet/layar pintar Android ke komputer lokal menggunakan kabel USB (atau ADB over Wi-Fi).
2. Pastikan USB Debugging aktif di tablet (`Developer Options > USB Debugging`).
3. Verifikasi koneksi perangkat:
   ```bash
   adb devices
   ```
4. Pasang APK internal ke perangkat:
   ```bash
   adb install -r android/app/build/outputs/apk/internal/app-internal.apk
   ```

*Perhatian*: Varian internal memiliki akhiran versi `-internal` namun mempertahankan Application ID produksi (`id.my.yhdev.nexo`) agar konfigurasi Firebase tetap valid. Jangan melakukan *uninstall* aplikasi sembarangan karena itu akan menghapus data kredensial token terenkripsi yang tersimpan di Keystore perangkat.

---

## 4. Kompilasi & Build Varian Production Release (Signed)

Varian `release` menghasilkan APK produksi yang siap pakai untuk deployment jangka panjang. Varian ini secara ketat **tidak mengizinkan fallback ke debug signing**. Tanpa konfigurasi kredensial keystore yang valid, Gradle akan menolak proses signing.

### Persiapan Kredensial Keystore Lokal:
Simpan file Keystore resmi (`.jks`) di lokasi aman pada komputer lokal Anda. **Jangan pernah mengunggah file keystore ke repositori Git publik atau server!**

Set empat environment variables berikut di sesi terminal lokal:

#### Linux / macOS:
```bash
export NEXO_KEYSTORE_PATH="/absolute/path/to/owner/keystore.jks"
export NEXO_KEYSTORE_PASSWORD="password_keystore_rahasia"
export NEXO_KEY_ALIAS="alias_kunci_nexo"
export NEXO_KEY_PASSWORD="password_alias_rahasia"
```

#### Windows PowerShell:
```powershell
$env:NEXO_KEYSTORE_PATH="C:\absolute\path\to\owner\keystore.jks"
$env:NEXO_KEYSTORE_PASSWORD="password_keystore_rahasia"
$env:NEXO_KEY_ALIAS="alias_kunci_nexo"
$env:NEXO_KEY_PASSWORD="password_alias_rahasia"
```

### Perintah Build Release:
```bash
cd android
./gradlew assembleRelease
# Windows: gradlew.bat assembleRelease
```

### Lokasi Output APK Production Release:
```text
android/app/build/outputs/apk/release/app-release.apk
```

---

## 5. Menjalankan Unit Test Lokal Android

Untuk memverifikasi logika unit test Android (seperti parser, audio manager, dan network client) di komputer lokal:

- **Linux / macOS**:
  ```bash
  cd android
  ./gradlew testDebugUnitTest
  ```

- **Windows**:
  ```cmd
  cd android
  gradlew.bat testDebugUnitTest
  ```

Laporan hasil pengujian HTML akan tersedia di `android/app/build/reports/tests/testDebugUnitTest/index.html`.

---

## 6. Verifikasi Statis Sisi Server (Static Analysis Guards)

Di lingkungan server VPS / CI, pemeriksaan integritas kode Android dilakukan melalui unit test Python tanpa Gradle:

```bash
# Dijalankan dari root repositori di server:
server/.venv/bin/pytest server/tests/test_android_static_guards.py -v
```

Pemeriksaan ini mencakup:
- Validasi struktur XML vektor dan layer-list seluruh drawable maskot.
- Integritas aset raw earcon (`chime_listening.wav`, `earcon_listening.wav`, dll.).
- Validasi sintaks dan kelas Kotlin tanpa kompilasi (`grep/regex` pattern check).
- Integritas manifest Android dan registrasi service kios.

---

## 7. Troubleshooting Umum Build Lokal

1. **Error `Unsupported class file major version 65` (Java Version Incompatible)**:
   - Penyebab: Menggunakan JDK 21 atau lebih baru yang belum didukung oleh Gradle 8.7.
   - Solusi: Pasang dan atur `JAVA_HOME` ke JDK 17. Di Android Studio, ubah Gradle JDK ke versi 17.
2. **Error `File google-services.json is missing`**:
   - Solusi: Pastikan `google-services.json` telah diletakkan di `android/app/`.
3. **Error `Gradle wrapper checksum mismatch`**:
   - Pinned SHA-256 Gradle 8.7 wrapper JAR:
     `cb0da6751c2b753a16ac168bb354870ebb1e162e9083f116729cec9c781156b8`
   - Pastikan wrapper JAR tidak dimodifikasi oleh proxy atau alat pihak ketiga.
4. **ADB Device Offline / Unauthorized**:
   - Cek layar tablet dan setujui dialog *Allow USB Debugging (Always allow from this computer)*.
5. **App Crash saat dibuka (Missing Token)**:
   - Aplikasi membutuhkan token autentikasi perangkat (Stage 1 Auth). Ikuti panduan [docs/DEVICE_AUTH.md](DEVICE_AUTH.md) untuk melakukan provision token melalui bot Telegram atau CLI server.
