#!/bin/bash
# SPADA Bot - GCP Free Tier Setup Script
# Jalankan sebagai root atau dengan sudo

set -e

echo "=========================================="
echo "  SPADA Bot - GCP Free Tier Setup"
echo "=========================================="

# Warna
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Cek apakah root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}Script ini harus dijalankan sebagai root${NC}"
   echo "Gunakan: sudo bash setup.sh"
   exit 1
fi

echo -e "${YELLOW}[1/8] Update system...${NC}"
apt update && apt upgrade -y

echo -e "${YELLOW}[2/8] Install dependencies...${NC}"
apt install -y python3.11 python3.11-venv python3-pip wget git curl

echo -e "${YELLOW}[3/8] Setup swap (untuk Playwright)...${NC}"
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "Swap 2GB created"
else
    echo "Swap sudah ada"
fi

echo -e "${YELLOW}[4/8] Clone bot repository...${NC}"
cd /home
if [ ! -d "spada-bot" ]; then
    # Clone dari GitHub atau copy dari local
    # git clone https://github.com/username/spada-bot.git
    echo "Repository belum dikloning. Silakan copy bot files ke /home/spada-bot/"
else
    echo "Repository sudah ada"
fi

echo -e "${YELLOW}[5/8] Setup virtual environment...${NC}"
cd /home/spada-bot
python3.11 -m venv venv
source venv/bin/activate

echo -e "${YELLOW}[6/8] Install Python dependencies...${NC}"
pip install --upgrade pip
pip install -r requirements.txt

echo -e "${YELLOW}[7/8] Install Playwright...${NC}"
playwright install chromium
playwright install-deps

echo -e "${YELLOW}[8/8] Setup systemd service...${NC}"
cp /home/spada-bot/spada-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable spada-bot

echo ""
echo "=========================================="
echo -e "${GREEN}Setup selesai!${NC}"
echo "=========================================="
echo ""
echo "Langkah selanjutnya:"
echo "1. Edit file .env dengan credentials:"
echo "   nano /home/spada-bot/.env"
echo ""
echo "2. Jalankan bot:"
echo "   sudo systemctl start spada-bot"
echo ""
echo "3. Cek status:"
echo "   sudo systemctl status spada-bot"
echo ""
echo "4. Lihat log:"
echo "   journalctl -u spada-bot -f"
echo ""
echo "=========================================="
