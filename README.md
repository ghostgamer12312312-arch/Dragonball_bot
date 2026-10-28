 🐉 Dragon Ball RPG Telegram Bot

A massive, feature-rich Dragon Ball RPG bot for Telegram. Includes PvP, Gacha, Clans, Raids, Exploration, OP Commands, and a persistent HP system.

## Features
- ⚔️ **Combat:** 3-option explore, Zenkai boosts, World Bosses.
- 🎰 **Gacha:** 90+ characters across 6 rarities (Omni, Mythical, Legendary, Epic, Rare, Common).
- 🏰 **Clans:** Full 9-command system with shared vaults, perks, and raids.
- 💥 **OP Commands:** `/duel`, `/bounty`, `/ultimate`, `/autofarm`, `/worldboss`.
- 📅 **Quests:** Daily, Weekly, and Monthly progression with auto-claim.
- 🛒 **Economy:** 6-category shop + secret Black Market, bulk buying, and gifting.

## 🛠️ Termux Installation Guide

This bot requires **Python 3.11** because the latest Termux Python (3.14) is currently incompatible with the Telegram library.

### Step 1: Install Dependencies
```bash
pkg update -y && pkg upgrade -y
pkg install python3.11 git nano -y
pkg install tur-repo -y
pkg install python3.11 -y
Step 2: Clone the Bot
bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
cd YOUR_REPO_NAME
Step 3: Create Virtual Environment
bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
Step 4: Setup Credentials
bash
cp .env.example .env
nano .env
Add your BOT_TOKEN (from @BotFather).

Add your OWNER_ID (from @userinfobot).

Press CTRL+O, Enter, then CTRL+X to save.

Step 5: Run the Bot
bash
sh start.sh
🛑 Keeping the Bot Online 24/7 (Termux)
If you close Termux, the bot will stop. Use tmux to keep it running.

bash
pkg install tmux -y
tmux new -s bot
sh start.sh
Press CTRL+B, then D to minimize.
To return to the bot, type: tmux attach -t bot.
