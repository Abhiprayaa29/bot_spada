# SPADA Telegram Bot

Telegram bot untuk reminder deadline dan auto absen di SPADA WIMAYA (UPNYK).

## Fitur

- Reminder deadline otomatis (30 menit sebelum)
- Auto absen saat jadwal kelas
- Screenshot bukti absen dikirim ke Telegram
- Commands: /dashboard, /deadlines, /courses, /absen, /absenall, /grades, /status

## Deploy ke GCP Free Tier

### Prasyarat
- Akun Google
- Google Cloud account (gratis)

### Langkah Cepat

1. Buka [Google Cloud Console](https://console.cloud.google.com/)
2. Buat project baru
3. Buat VM instance e2-micro (Ubuntu 22.04)
4. SSH ke instance
5. Jalankan:

```bash
# Clone repository
git clone https://github.com/USERNAME/spada-bot.git
cd spada-bot

# Jalankan setup
sudo bash setup.sh

# Edit credentials
nano .env

# Jalankan bot
sudo systemctl start spada-bot
```

### Cek Status

```bash
sudo systemctl status spada-bot
journalctl -u spada-bot -f
```

### Restart Bot

```bash
sudo systemctl restart spada-bot
```

## Setup Lokal

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Buat .env
cp .env.example .env
nano .env

# Jalankan
python bot.py
```

## Struktur File

```
bot_spada/
├── bot.py              # Telegram bot
├── config.py           # Konfigurasi
├── spada.py            # SPADA scraper
├── .env                # Credentials (jangan di-commit)
├── .env.example        # Template credentials
├── requirements.txt    # Dependencies
├── setup.sh            # Setup script untuk GCP
├── spada-bot.service   # Systemd service
├── DEPLOY_GCP.md       # Guide deploy ke GCP
└── .gitignore          # Git ignore
```

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Mulai bot |
| `/dashboard` | Ringkasan SPADA |
| `/deadlines` | Lihat deadline mendatang |
| `/courses` | Daftar mata kuliah |
| `/absen [nama]` | Absen kelas tertentu |
| `/absenall` | Absen semua kelas hari ini |
| `/grades` | Lihat nilai |
| `/status` | Status bot |

## Auto Features

- Reminder deadline otomatis (30 menit sebelum)
- Auto absen 15 menit sebelum kelas berakhir
- Screenshot bukti absen dikirim ke chat

## Troubleshooting

```bash
# Lihat log
journalctl -u spada-bot -f

# Restart bot
sudo systemctl restart spada-bot

# Cek status
sudo systemctl status spada-bot
```

## Catatan

- Bot berjalan 24/7 di GCP Free Tier
- Auto-restart jika crash
- Gratis selamanya (Always Free)
