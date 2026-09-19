# SPADA Telegram Bot

Telegram bot untuk mengelola kuliah di SPADA WIMAYA (UPNYK). Fitur lengkap mulai dari reminder deadline, auto absen, sinkronisasi nilai, hingga briefing harian — semuanya otomatis.

---

## Daftar Isi

- [Fitur Utama](#fitur-utama)
- [Cara Kerja](#cara-kerja)
- [Prasyarat](#prasyarat)
- [Instalasi](#instalasi)
  - [Setup Lokal](#setup-lokal)
  - [Deploy ke GCP Free Tier](#deploy-ke-gcp-free-tier)
- [Konfigurasi](#konfigurasi)
- [Commands](#commands)
- [Fitur Otomatis](#fitur-otomatis)
- [Struktur File](#struktur-file)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Catatan](#catatan)

---

## Fitur Utama

| Fitur | Deskripsi |
|-------|-----------|
| **Login/Logout** | Login via Telegram dengan validasi SPADA + BIMA sekaligus |
| **Auto Deteksi Semester** | Otomatis mendeteksi semester aktif dari BIMA |
| **Filter Mata Kuliah BIMA** | Hanya menampilkan mata kuliah yang terdaftar di BIMA (semester aktif) |
| **Dashboard** | Ringkasan lengkap: jadwal hari ini, tugas pending, nilai terkini |
| **Deadline Reminder** | Notifikasi otomatis 24 jam, 1 jam, dan 15 menit sebelum deadline |
| **Auto Absen** | Absen otomatis 5 menit sebelum kelas berakhir |
| **Screenshot Bukti** | Screenshot bukti absen dikirim langsung ke Telegram |
| **Daily Briefing** | Briefing harian jam 07:00 WIB: jadwal, tugas pending, nilai |
| **Sinkronisasi** | Sync data SPADA ke tracker lokal untuk tracking submission |
| **Tugas & Nilai** | Lihat semua tugas, status submission, dan nilai |

---

## Cara Kerja

1. **Login** — Ketik `/login` di Telegram, masukkan NIM dan password SPADA
2. **Validasi BIMA** — Bot otomatis login ke BIMA untuk mendeteksi semester aktif dan daftar mata kuliah
3. **Filter Otomatis** — Semua fitur (dashboard, deadline, tugas, absen) hanya menampilkan mata kuliah semester aktif yang terdaftar di BIMA
4. **Auto Reminder** — Bot mengecek deadline setiap 30 menit, kirim notifikasi saat mendekati waktu submit
5. **Auto Absen** — Bot mengecek jadwal setiap 5 menit, absen otomatis saat masuk window 5 menit sebelum kelas berakhir

---

## Prasyarat

| Komponen | Keterangan |
|----------|------------|
| **Python 3.10+** | Runtime untuk bot |
| **Telegram Bot Token** | Dari [@BotFather](https://t.me/BotFather) |
| **Telegram Chat ID** | Dari [@userinfobot](https://t.me/userinfobot) |
| **Akun SPADA** | NIM dan password SPADA UPNYK |
| **DrissionPage + Chromium** | Untuk scraping BIMA (login otomatis, anti reCAPTCHA) |

---

## Instalasi

### Setup Lokal

```bash
# 1. Clone repository
git clone https://github.com/YOUR_USERNAME/spada-bot.git
cd spada-bot

# 2. Buat virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install dependencies sudah termasuk DrissionPage (otomatis install Chromium)

# 5. Buat file .env
cp .env.example .env

# 6. Edit .env — isi 2 variabel saja:
#    TELEGRAM_BOT_TOKEN=token_dari_botfather
#    TELEGRAM_CHAT_ID=id_chat_dari_userinfobot
nano .env

# 7. Jalankan bot
python bot.py
```

Setelah bot berjalan, buka Telegram dan ketik `/login` untuk masuk dengan akun SPADA.

### Deploy ke GCP Free Tier

```bash
# 1. Buat VM instance e2-micro (Ubuntu 22.04) di Google Cloud Console

# 2. SSH ke instance

# 3. Clone repository
git clone https://github.com/YOUR_USERNAME/spada-bot.git
cd spada-bot

# 4. Jalankan setup script
sudo bash setup.sh

# 5. Edit .env
nano .env
# Isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID

# 6. Jalankan service
sudo systemctl start spada-bot
sudo systemctl enable spada-bot  # agar otomatis start saat reboot
```

#### Perintah Service

```bash
# Cek status
sudo systemctl status spada-bot

# Lihat log real-time
journalctl -u spada-bot -f

# Restart
sudo systemctl restart spada-bot

# Stop
sudo systemctl stop spada-bot
```

---

## Konfigurasi

### File `.env`

Hanya ada 2 variabel yang perlu diisi:

```env
# Telegram Bot Token (dari @BotFather)
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Telegram Chat ID (dari @userinfobot)
TELEGRAM_CHAT_ID=your_chat_id_here
```

> **Catatan:** SPADA username dan password **tidak perlu** diisi di `.env`. Cukup ketik `/login` di Telegram setelah bot berjalan.

### Konfigurasi Lanjutan

Di dalam `config.py`, ada beberapa pengaturan yang bisa diubah:

| Variabel | Default | Deskripsi |
|----------|---------|-----------|
| `REMINDER_CHECK_INTERVAL_MINUTES` | 30 | Interval pengecekan deadline (menit) |
| `AUTO_ATTENDANCE_ENABLED` | `True` | Aktifkan/auto absen |
| `ATTENDANCE_WINDOW_MINUTES` | 5 | Window absen otomatis (menit sebelum kelas berakhir) |

---

## Commands

| Command | Deskripsi | Contoh |
|---------|-----------|--------|
| `/start` | Mulai bot, tampilkan menu bantuan | `/start` |
| `/login` | Login ke SPADA + BIMA | `/login` |
| `/logout` | Logout, hapus session tersimpan | `/logout` |
| `/dashboard` | Ringkasan: jadwal hari ini, tugas pending, nilai | `/dashboard` |
| `/deadlines` | Lihat deadline mendatang (filtered by BIMA) | `/deadlines` |
| `/courses` | Daftar semua mata kuliah per semester | `/courses` |
| `/courses 5` | Lihat mata kuliah semester tertentu | `/courses 5` |
| `/tugas` | Daftar tugas lengkap dengan status submission | `/tugas` |
| `/absen [nama]` | Absen manual untuk kelas tertentu | `/absen Kriptografi` |
| `/absenall` | Absen semua kelas hari ini | `/absenall` |
| `/grades` | Lihat nilai dari semua mata kuliah | `/grades` |
| `/sync` | Sinkronisasi data SPADA ke tracker lokal | `/sync` |
| `/status` | Status bot, semester, jumlah matkul BIMA | `/status` |
| `/briefing` | Kirim briefing harian secara manual | `/briefing` |
| `/help` | Tampilkan daftar command | `/help` |

---

## Fitur Otomatis

### Reminder Deadline

Bot mengecek deadline setiap **30 menit** dan mengirim notifikasi:

| Waktu | Notifikasi |
|-------|------------|
| 24 jam sebelum deadline | `⏰ 24 jam lagi!` |
| 1 jam sebelum deadline | `⚠️ 1 jam lagi!` |
| 15 menit sebelum deadline | `🚨 15 menit lagi!` |

### Auto Absen

Bot mengecek jadwal kelas setiap **5 menit**. Saat waktu masuk window 5 menit sebelum kelas berakhir:

1. Bot otomatis submit absen di SPADA
2. Bot mengambil screenshot bukti absen
3. Screenshot dikirim ke Telegram sebagai bukti

### Daily Briefing

Setiap jam **07:00 WIB** (00:00 UTC), bot mengirim briefing harian:

- Jadwal kelas hari ini
- Daftar tugas pending
- Ringkasan nilai terkini

### Filtering BIMA

Semua data (jadwal, deadline, tugas, absen) **di-filter berdasarkan mata kuliah yang terdaftar di BIMA**. Ini memastikan hanya mata kuliah semester aktif yang ditampilkan.

---

## Struktur File

```
bot_spada/
├── bot.py              # Telegram bot (main entry point)
├── bima.py             # BIMA scraper (DrissionPage CDP stealth + reCAPTCHA)
├── spada.py            # SPADA scraper (requests + BeautifulSoup)
├── config.py           # Konfigurasi dari .env + store.py
├── store.py            # Penyimpanan session (data/session.json)
├── tracker.py          # Local tracker untuk tugas/submission/nilai
├── data/               # Data runtime (di-.gitignore)
│   ├── session.json    # Session SPADA + BIMA
│   ├── bima_cookies.json  # Cookie BIMA
│   └── tugas_tracker.json # Tracker tugas lokal
├── .env                # Credentials 
├── .env.example        # Template .env
├── .gitignore          # File yang di-exclude dari git
├── requirements.txt    # Python dependencies
├── setup.sh            # Setup script untuk GCP
├── spada-bot.service   # Systemd service file
└── README.md           # Dokumentasi ini
```

---

## Troubleshooting

### Bot tidak menyala

```bash
# Cek apakah process berjalan
pgrep -af "bot.py"

# Cek log
tail -50 /tmp/bot_spada.log

# Restart
python bot.py
```

### Login gagal

1. Pastikan NIM dan password benar
2. Coba login langsung di https://spada.upnyk.ac.id untuk memastikan akun aktif
3. Jika BIMA gagal, bot tetap bisa jalan tanpa filter BIMA

### Auto absen tidak jalan

1. Pastikan `/status` menunjukkan `Auto Absen: ON`
2. Pastikan attendance map sudah terisi (lihat `/absen` tanpa argumen)
3. Cek apakah jadwal sudah benar di `data/session.json`

### DrissionPage error

DrissionPage menggunakan Chromium secara langsung via CDP (Chrome DevTools Protocol). Jika terjadi error:

```bash
# Pastikan Chromium terinstall
which chromium || sudo apt-get install -y chromium

# Install dependencies system (Ubuntu)
sudo apt-get install -y libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
  libcups2 libdrm2 libdbus-1-3 libxkbcommon0 libatspi2.0-0 \
  libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
  libcairo2 libasound2
```

---

## FAQ

**Q: Apakah password SPADA disimpan di `.env`?**
A: Tidak. Password hanya diinput via `/login` di Telegram dan disimpan di `data/session.json` (yang di-.gitignore).

**Q: Apakah bot bisa jalan tanpa BIMA?**
A: Ya. Jika login BIMA gagal, bot tetap berjalan tapi tanpa filter semester. Semua mata kuliah akan ditampilkan.

**Q: Berapa resource yang dibutuhkan?**
A: Sangat ringan. Cukup 1 CPU, 512MB RAM (GCP e2-micro gratis).

**Q: Apakah aman untuk di-share ke GitHub?**
A: Ya. File `.env` dan `data/` sudah di-.gitignore. Tidak ada credential yang ter-commit.

**Q: Bagaimana cara ganti password SPADA?**
A: Ketik `/logout` lalu `/login` lagi dengan credential baru.

---

## Catatan

- Bot berjalan 24/7 di GCP Free Tier (Always Free)
- Auto-restart saat crash (systemd)
- File `data/session.json` menyimpan session agar bot tidak perlu login ulang setiap restart
- BIMA menggunakan DrissionPage (CDP stealth) untuk bypass reCAPTCHA v2
- Screenshot bukti absen disimpan di folder `screenshots/` (otomatis dihapus setelah dikirim)

---

## Lisensi

Personal use only. Dibuat untuk kebutuhan kuliah di UPNYK.
