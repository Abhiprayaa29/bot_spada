# SPADA Telegram Bot

Telegram bot + web dashboard untuk mengelola kuliah di SPADA WIMAYA (UPNYK). Login otomatis, reminder deadline, auto absen, sinkronisasi nilai, input jadwal manual via web — semuanya dari satu tempat.

---

## Daftar Isi

- [Fitur Utama](#fitur-utama)
- [Cara Kerja](#cara-kerja)
- [Prasyarat](#prasyarat)
- [Instalasi](#instalasi)
  - [Setup Lokal](#setup-lokal)
  - [Deploy ke GCP Free Tier](#deploy-ke-gcp-free-tier)
- [Web Dashboard](#web-dashboard)
- [Input Jadwal (BIMA)](#input-jadwal-bima)
- [Konfigurasi](#konfigurasi)
- [Commands](#commands)
- [Fitur Otomatis](#fitur-otomatis)
- [Struktur File](#struktur-file)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

---

## Fitur Utama

| Fitur | Deskripsi |
|-------|-----------|
| **Login/Logout** | Login via Telegram dengan validasi SPADA |
| **Auto Deteksi Semester** | Otomatis mendeteksi semester aktif dari SPADA |
| **Web Dashboard** | Dashboard read-only dengan QR pairing — lihat jadwal, tugas, nilai, absensi |
| **Input Jadwal Manual** | Input jadwal dari BIMA/SPADA langsung di web atau via Telegram |
| **Jadwal-Gated Features** | Absensi dan tugas hanya muncul setelah jadwal diinput |
| **Deadline Reminder** | Notifikasi otomatis 24 jam, 1 jam, dan 15 menit sebelum deadline |
| **Auto Absen** | Absen otomatis 5 menit sebelum kelas berakhir |
| **Screenshot Bukti** | Screenshot bukti absen dikirim langsung ke Telegram |
| **Daily Briefing** | Briefing harian jam 07:00 WIB: jadwal, tugas pending, nilai |
| **Sinkronisasi** | Sync data SPADA ke tracker lokal untuk tracking submission |
| **Tugas & Nilai** | Lihat semua tugas, status submission, dan nilai |

---

## Cara Kerja

1. **Login** — Ketik `/login` di Telegram, masukkan NIM dan password SPADA
2. **Input Jadwal** — Salin jadwal dari BIMA/SPADA, paste di web dashboard atau via `/setjadwal`
3. **Auto Semester** — Bot otomatis mendeteksi semester aktif dari SPADA
4. **Auto Reminder** — Bot mengecek deadline setiap 30 menit, kirim notifikasi saat mendekati waktu submit
5. **Auto Absen** — Bot mengecek jadwal setiap 5 menit, absen otomatis saat masuk window 5 menit sebelum kelas berakhir

> **Catatan:** Absensi dan tugas hanya aktif setelah jadwal diinput. Jika belum ada jadwal, menu absensi dan tugas tidak akan menampilkan data.

---

## Prasyarat

| Komponen | Keterangan |
|----------|------------|
| **Python 3.10+** | Runtime untuk bot |
| **Chromium** | Untuk SPADA scraper (DrissionPage) |
| **Telegram Bot Token** | Dari [@BotFather](https://t.me/BotFather) |
| **Telegram Chat ID** | Dari [@userinfobot](https://t.me/userinfobot) |
| **Akun SPADA** | NIM dan password SPADA UPNYK |

---

## Instalasi

### Setup Lokal

```bash
# 1. Clone repository
git clone https://github.com/Abhiprayaa29/bot_spada.git
cd bot_spada

# 2. Buat virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac

# 3. Install dependencies
pip install -r requirements.txt

# 4. Buat file .env
cp .env.example .env
nano .env
# Isi: TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID

# 5. Jalankan bot
python bot.py

# 6. Jalankan web dashboard (terminal terpisah)
python -m uvicorn web.main:app --host 0.0.0.0 --port 8088
```

Setelah bot berjalan, buka Telegram dan ketik `/login` untuk masuk dengan akun SPADA.

### Deploy ke GCP Free Tier

```bash
# 1. Buat VM instance e2-micro (Ubuntu 22.04) di Google Cloud Console

# 2. SSH ke instance

# 3. Clone repository
git clone https://github.com/Abhiprayaa29/bot_spada.git
cd bot_spada

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

## Web Dashboard

Dashboard web read-only untuk melihat data SPADA tanpa harus buka Telegram.

### Fitur Dashboard

| Halaman | Deskripsi |
|---------|-----------|
| **Dashboard** (`/`) | Ringkasan: jadwal hari ini, tugas pending, nilai |
| **Jadwal** (`/jadwal`) | Daftar lengkap jadwal per hari |
| **Input Jadwal** (`/jadwal-input`) | Form untuk paste jadwal dari BIMA/SPADA |
| **Tugas** (`/tugas`) | Daftar tugas pending & sudah submit |
| **Nilai** (`/nilai`) | Daftar nilai dari SPADA dan BIMA |
| **Absensi** (`/absensi`) | Riwayat absensi dan attendance IDs |

### Pairing QR Code

1. Bot di Telegram kirim QR code saat `/login`
2. Buka halaman pairing di web: `http://localhost:8088/pair`
3. Scan QR code dari Telegram
4. Session tersimpan di cookie, tidak perlu login ulang

### Menjalankan Web Dashboard

```bash
# Development
python -m uvicorn web.main:app --host 0.0.0.0 --port 8088 --reload

# Production (dengan tmux/screen)
tmux new-session -d -s web "python -m uvicorn web.main:app --host 0.0.0.0 --port 8088"
```

---

## Input Jadwal (BIMA)

Absensi dan tugas hanya muncul setelah jadwal diinput. Ada 2 cara:

### Cara 1: Via Web Dashboard (Recommended)

1. Buka `http://localhost:8088/jadwal-input`
2. Salin jadwal dari BIMA/SPADA (format tab-separated)
3. Paste di textarea, klik **Simpan**
4. Jadwal otomatis tersimpan di `data/bima_schedule.json`

### Cara 2: Via Telegram

```
/setjadwal
[ paste jadwal di sini ]
```

### Format Jadwal

Jadwal diambil dari halaman BIMA/SPADA dalam format tab-separated:

```
IF21	120210032	Kapita Selekta	IF-A	2
Sabtu 07:30 - 09:15 Patt.I-3A

Awang Hendrianto P. Dr. S.T., M.T.

0
```

**Tips:** Buka halaman jadwal di browser, pilih semua (Ctrl+A), copy (Ctrl+C), lalu paste di form.

---

## Konfigurasi

### File `.env`

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

### Akun

| Command | Deskripsi |
|---------|-----------|
| `/start` | Mulai bot, tampilkan menu bantuan |
| `/login` | Login ke SPADA (kirim QR code untuk web pairing) |
| `/logout` | Logout, hapus session tersimpan |
| `/help` | Tampilkan daftar command |

### Informasi

| Command | Deskripsi |
|---------|-----------|
| `/dashboard` | Ringkasan: jadwal hari ini, tugas pending, nilai |
| `/deadlines` | Lihat deadline mendatang |
| `/courses` | Daftar semua mata kuliah per semester |
| `/courses [semester]` | Filter mata kuliah per semester |
| `/status` | Status bot, semester, jumlah matkul |
| `/semester` | Info semester aktif dari SPADA |
| `/briefing` | Ringkasan harian |
| `/sync` | Sinkronisasi data SPADA ke tracker lokal |

### Input Manual

| Command | Deskripsi |
|---------|-----------|
| `/setjadwal` | Input jadwal dari BIMA/SPADA (paste text) |
| `/setsemester [kode]` | Set kode semester manual |
| `/listjadwal` | Lihat jadwal tersimpan |
| `/bima` | Lihat status jadwal & nilai BIMA |

### Presensi & Tugas

| Command | Deskripsi |
|---------|-----------|
| `/tugas` | Lihat semua tugas dengan status submission |
| `/absen [nama_kelas]` | Absen manual untuk kelas tertentu |

> **Catatan:** `/tugas` dan `/absen` hanya berfungsi setelah jadwal diinput.

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

---

## Struktur File

```
bot_spada/
├── bot.py                  # Telegram bot (main entry point)
├── spada.py                # SPADA scraper (DrissionPage + requests)
├── bima.py                 # Parser jadwal manual dari BIMA/SPADA
├── config.py               # Konfigurasi dari .env + store.py
├── store.py                # Penyimpanan session (data/session.json)
├── tracker.py              # Local tracker untuk tugas/submission/nilai
├── pairing_store.py        # QR pairing token storage
├── web/                    # FastAPI web dashboard
│   ├── main.py             # Routes, pages, API endpoints
│   ├── auth.py             # Cookie-based session auth
│   ├── pairing.py          # QR code generation & token validation
│   ├── data_reader.py      # Safe JSON file reader
│   ├── static/style.css    # Dashboard CSS
│   └── templates/          # (unused — inline HTML due to Jinja2 compat)
├── data/                   # Data runtime (di-.gitignore)
│   ├── session.json        # Session SPADA (credentials, semester, schedule)
│   ├── tugas_tracker.json  # Tracker tugas/submission/nilai
│   ├── bima_schedule.json  # Jadwal dari BIMA (manual input)
│   ├── bima_grades.json    # Nilai dari BIMA
│   ├── bima_cookies.json   # (legacy, tidak dipakai)
│   └── pairing_tokens.json # QR pairing tokens
├── Caddyfile               # Caddy reverse proxy config
├── .env                    # Credentials
├── .env.example            # Template .env
├── .gitignore              # File yang di-exclude dari git
├── requirements.txt        # Python dependencies
├── setup.sh                # Setup script untuk GCP
├── spada-bot.service       # Systemd service file
└── README.md               # Dokumentasi ini
```

---

## Troubleshooting

### Bot tidak menyala

```bash
# Cek apakah process berjalan
pgrep -af "bot.py"

# Jalankan di foreground untuk lihat error
source venv/bin/activate && python bot.py
```

### Login gagal

1. Pastikan NIM dan password benar
2. Coba login langsung di https://spada.upnyk.ac.id untuk memastikan akun aktif
3. Jika muncul `httpx.ConnectError`, coba login ulang (error transient)

### Absensi / Tugas tidak muncul

1. Pastikan jadwal sudah diinput (cek `/bima` atau `/listjadwal`)
2. Jika belum ada jadwal, input via `/setjadwal` atau web dashboard `/jadwal-input`

### Auto absen tidak jalan

1. Pastikan `/status` menunjukkan `Auto Absen: ON`
2. Pastikan attendance map sudah terisi (lihat `/absen` tanpa argumen)
3. Pastikan jadwal sudah benar di `data/session.json`

### Web dashboard tidak bisa diakses

1. Pastikan uvicorn berjalan: `curl http://localhost:8088/dashboard`
2. Jika menggunakan Caddy, pastikan Caddy berjalan: `pgrep caddy`
3. Pairing expired? Login ulang di Telegram untuk dapat QR code baru

### DrissionPage error

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

**Q: Berapa resource yang dibutuhkan?**
A: Sangat ringan. Cukup 1 CPU, 512MB RAM (GCP e2-micro gratis).

**Q: Apakah aman untuk di-share ke GitHub?**
A: Ya. File `.env`, `data/`, dan credentials sudah di-.gitignore. Tidak ada credential yang ter-commit.

**Q: Bagaimana cara ganti password SPADA?**
A: Ketik `/logout` lalu `/login` lagi dengan credential baru.

**Q: Kenapa absensi tidak muncul?**
A: Absensi dan tugas hanya muncul setelah jadwal diinput. Gunakan `/setjadwal` atau buka web dashboard `/jadwal-input` untuk input jadwal terlebih dahulu.

**Q: Format jadwal seperti apa yang diterima?**
A: Format tab-separated dari BIMA/SPADA. Buka halaman jadwal di browser, pilih semua (Ctrl+A), copy, lalu paste di form input atau via `/setjadwal`.

**Q: Bisakah import jadwal dari file?**
A: Bisa. Salin isi jadwal dari browser, paste langsung ke textarea di web dashboard atau via `/setjadwal` di Telegram.

---

## Lisensi

Personal use only. Dibuat untuk kebutuhan kuliah di UPNYK.
