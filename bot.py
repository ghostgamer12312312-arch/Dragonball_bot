from __future__ import annotations

"""
Dragon Ball RPG Telegram Bot — Main Entry (PART 1 of 8)
=========================================================
This file is massive and contains all OP commands and features.
"""

import os
import random
import time
import logging
import traceback
import asyncio
from datetime import datetime, timedelta, timezone
from html import escape

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    KeyboardButton, ReplyKeyboardMarkup
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    MessageHandler, filters, CallbackQueryHandler
)

import clans
import combat
import db
import game_data as G

# =============================================================
# LOGGING SETUP
# =============================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =============================================================
# CONSTANTS & GLOBAL STATE
# =============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)

EXPLORE_STATES: dict[int, dict] = {}
BUY_STATES: dict[int, dict] = {}
FUSION_STATES: dict[int, dict] = {}
CLAN_MGMT_STATES: dict[int, dict] = {}
DUEL_STATES: dict[int, dict] = {}

# =============================================================
# ROBUST ERROR HANDLING DECORATOR
# =============================================================
def handle_errors(func):
    """Catches all exceptions and prevents the bot from crashing."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            await func(update, context)
        except Exception as e:
            logger.error(f"Exception in {func.__name__}: {e}")
            logger.error(traceback.format_exc())
            error_msg = str(e)
            if "database is locked" in error_msg.lower():
                user_msg = "⚠️ Database is busy. Please try again in 2 seconds."
            elif "Message is not modified" in error_msg:
                return  # Ignore Telegram edit errors
            else:
                user_msg = f"⚠️ <b>An error occurred.</b>\n<code>{escape(error_msg)}</code>"
            
            if isinstance(update, Update) and update.effective_message:
                try:
                    await update.effective_message.reply_text(user_msg, parse_mode=ParseMode.HTML)
                except Exception:
                    pass
    return wrapper

# =============================================================
# PERMISSION DECORATORS
# =============================================================
def require_admin(func):
    @handle_errors
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if not (is_owner(uid) or is_admin(uid)):
            await update.effective_message.reply_text("⛔ You are not allowed to use admin commands.")
            return
        await func(update, context)
    return wrapper

def require_owner(func):
    @handle_errors
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if not is_owner(uid):
            await update.effective_message.reply_text("⛔ Only the bot owner can use this command.")
            return
        await func(update, context)
    return wrapper

def require_not_banned(func):
    @handle_errors
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if is_banned(uid):
            await update.effective_message.reply_text("🚫 You are banned from using this bot.")
            return
        await func(update, context)
    return wrapper

# =============================================================
# HELPER FUNCTIONS
# =============================================================
def is_owner(uid: int) -> bool: return uid == OWNER_ID
def is_admin(uid: int) -> bool:
    p = db.player(uid)
    return bool(p and p["admin"])
def is_banned(uid: int) -> bool:
    p = db.player(uid)
    return bool(p and p["banned"])

def ensure_player_for_update(update: Update) -> None:
    user = update.effective_user
    if user is None: return
    db.ensure_player(user.id, user.first_name or "Player", user.username)
    db.ensure_quests_current(user.id)

def resolve_target_id(args: list[str], update: Update) -> int | None:
    if update.message and update.message.reply_to_message:
        u = update.message.reply_to_message.from_user
        if u and not u.is_bot: return u.id
    if not args: return None
    tok = args[0].strip()
    if not tok: return None
    if tok.startswith("@"):
        row = db.player_by_username(tok[1:])
        return row["user_id"] if row else None
    if tok.isdigit(): return int(tok)
    return None

def parse_amount(value: str) -> int:
    try: return max(1, int(value))
    except ValueError: return 0

def parse_amount_zero_ok(value: str) -> int:
    try: return max(0, int(value))
    except ValueError: return 0

def resource_name(name: str) -> str | None:
    aliases = {
        "yen": "yens", "yens": "yens", "money": "yens",
        "gem": "gems", "gems": "gems",
        "crystal": "crystals", "crystals": "crystals",
        "ticket": "tickets", "tickets": "tickets",
        "skill_point": "skill_points", "skill_points": "skill_points", "sp": "skill_points",
        "elite_key": "keys_elite", "elite_keys": "keys_elite",
        "boss_key": "keys_boss", "boss_keys": "keys_boss",
        "exp": "exp",
    }
    return aliases.get((name or "").strip().lower())

def get_world_multiplier(uid: int) -> float:
    p = db.player(uid)
    if not p: return 1.0
    return G.PLANETS.get(p["travel"] or "Earth", G.PLANETS["Earth"])["multiplier"]

def _char_stats_by_name(name: str) -> tuple[int, int, int, int]:
    for row in G.CHARACTERS:
        if row[0] == name: return (row[2], row[3], row[4], row[5])
    return (0, 0, 0, 0)

def player_stats(uid: int) -> dict:
    p = db.player(uid)
    if not p: return {"atk": 0, "hp": 0, "spd": 0, "crit": 0, "level": 1, "rank": 1, "char_rarity": None, "current_hp": 0, "max_hp": 0}
    
    lvl = db.level_of(p["exp"])
    atk = 10 + lvl * 3
    hp = 100 + lvl * 12
    spd = 10 + lvl // 3
    crit = 5

    if p["weapon"] in G.EQUIPMENT: atk += G.EQUIPMENT[p["weapon"]][1]
    if p["armor"] in G.EQUIPMENT: hp += G.EQUIPMENT[p["armor"]][1] * 2

    char_rarity = None
    if p["equipped_char"]:
        for name, rarity in db.characters(uid):
            if name == p["equipped_char"]:
                char_rarity = rarity
                chp, catk, cspd, ccrit = _char_stats_by_name(name)
                hp += chp; atk += catk; spd += cspd; crit += ccrit
                break

    atk += sum(G.SKILL_ATK.get(s, 0) for s in db.skills(uid))
    
    rank = int(p["rank"] or 1)
    rank_mult = 1 + (rank - 1) * G.RANK_BONUS
    atk = int(atk * rank_mult)
    hp = int(hp * rank_mult)
    current_hp = db.get_current_hp(uid, hp)

    return {"atk": atk, "hp": hp, "spd": spd, "crit": crit, "level": lvl, "rank": rank, "char_rarity": char_rarity, "current_hp": current_hp, "max_hp": hp}

def _hp_bar(cur: int, mx: int) -> str:
    if mx <= 0: return "▯▯▯▯▯▯▯▯▯▯ 0%"
    pct = max(0, min(100, int(cur * 100 / mx)))
    filled = pct // 10
    return "█" * filled + "▯" * (10 - filled) + f" {pct}%"

def _progress_bar(cur: int, target: int) -> str:
    if target <= 0: return "0%"
    pct = min(100, int(cur * 100 / target))
    filled = pct // 10
    return f"{'█' * filled}{'▯' * (10 - filled)} {min(cur, target)}/{target}"

# =============================================================
# OP BATTLE EXECUTION (Zenkai & Ultimate integrated)
# =============================================================
def run_battle(uid, enemy_name, enemy_hp, enemy_dmg, enrage=False, is_boss=False, is_summon=False, skip_hp=False, is_ultimate=False):
    s = player_stats(uid)
    start_hp = s["max_hp"] if skip_hp else s["current_hp"]
    if start_hp <= 0: start_hp = s["max_hp"]

    # OP Feature: Zenkai Boost
    atk_to_use = s["atk"]
    if start_hp <= (s["max_hp"] * 0.20):
        atk_to_use = int(atk_to_use * 1.35)

    # OP Feature: Ultimate Attack (Guaranteed Crit & Massive Damage)
    crit_to_use = s["crit"]
    if is_ultimate:
        atk_to_use = int(atk_to_use * 2.5)
        crit_to_use = 100

    eng = combat.CombatEngine(atk_to_use, start_hp, crit=crit_to_use, speed=s["spd"])
    won, left, log = eng.fight(enemy_name, enemy_hp, enemy_dmg, enrage)
    outcome = "victory" if won else "defeat"

    hist = combat.CombatHistory(uid)
    hist.log(enemy_name, enemy_hp, eng.damage_dealt, eng.damage_taken, outcome, eng.rounds)

    if skip_hp: db.set_current_hp(uid, s["max_hp"])
    else:
        new_hp = 1 if (not won and left <= 0) else left
        db.set_current_hp(uid, max(1, new_hp))

    return {"won": won, "left_hp": left, "log": log, "dealt": eng.damage_dealt, "taken": eng.damage_taken, "rounds": eng.rounds}

def apply_drops(uid, is_boss, is_summon=False):
    drops = []
    drop_chance = 0.35 if (is_boss or is_summon) else 0.15
    if random.random() < drop_chance:
        item = random.choice(list(G.EQUIPMENT)); db.add_item(uid, item); drops.append(f"📦 {item}")
    db_chance = 0.45 if (is_boss or is_summon) else 0.10
    if random.random() < db_chance:
        star = random.choice(G.DRAGON_BALL_STARS); db.add_dragon_ball(uid, star); drops.append(f"🐉 Dragon Ball {star}★")
    ticket_chance = 0.30 if (is_boss or is_summon) else 0.05
    if random.random() < ticket_chance:
        db.give(uid, tickets=1); drops.append("🎫 Ticket")
    skill_chance = 0.20 if is_boss else 0.02
    if random.random() < skill_chance:
        skill = random.choice(list(G.SKILLS))
        if db.add_skill(uid, skill): drops.append(f"🌟 Skill: {skill}")
    if is_boss and random.random() < 0.25:
        mat = random.choice(list(G.FUSION_MATERIALS)); db.add_item(uid, mat); drops.append(f"🧬 {mat}")
    return drops

# =============================================================
# INLINE KEYBOARD MENUS
# =============================================================
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("👤 Profile", callback_data="profile"), InlineKeyboardButton("⚔️ Explore", callback_data="explore")],
        [InlineKeyboardButton("🛒 Shop", callback_data="shop"), InlineKeyboardButton("🎰 Gacha", callback_data="gacha")],
        [InlineKeyboardButton("🧙 Characters", callback_data="mycharacters"), InlineKeyboardButton("🏰 Clan", callback_data="clan")],
        [InlineKeyboardButton("🎒 Inventory", callback_data="inventory"), InlineKeyboardButton("📅 Quests", callback_data="quests")],
        [InlineKeyboardButton("🔱 Raids & Bosses", callback_data="raid"), InlineKeyboardButton("🌍 Travel", callback_data="travel")],
        [InlineKeyboardButton("🧪 Healing", callback_data="heal"), InlineKeyboardButton("🏆 Achievements", callback_data="achievements")],
        [InlineKeyboardButton("🔨 Crafting", callback_data="craft"), InlineKeyboardButton("🎫 Titles", callback_data="titles")],
        [InlineKeyboardButton("⚙️ Settings / Help", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)

# =============================================================
# START / HELP COMMANDS
# =============================================================
@require_not_banned
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)

    if p and int(p["exp"] or 0) == 0 and int(p["total_battles"] or 0) == 0:
        rarity = random.choice(G.STARTER_PACK["pool"])
        pool = [n for n, r, *_ in G.CHARACTERS if r == rarity]
        if pool:
            name = random.choice(pool)
            db.add_character(uid, name, rarity)
            db.set_field(uid, "equipped_char", name)
            db.give(uid, yens=G.STARTER_PACK["bonus_yens"], gems=G.STARTER_PACK["bonus_gems"], tickets=G.STARTER_PACK["bonus_tickets"])
            await update.message.reply_text(
                f"🎁 <b>Starter Pack Unlocked!</b>\n\n"
                f"You received <b>{escape(name)}</b> ({rarity})!\n"
                f"Automatically equipped as your lead warrior.\n"
                f"+{G.STARTER_PACK['bonus_yens']} yens, +{G.STARTER_PACK['bonus_gems']} gems, +{G.STARTER_PACK['bonus_tickets']} tickets.",
                parse_mode=ParseMode.HTML
            )

    await update.message.reply_text(
        "🏠 <b>Main Menu</b>\nSelect an option:",
        reply_markup=get_main_menu(),
        parse_mode=ParseMode.HTML
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>How to Play</b>\n\n"
        "<b>Combat</b>\n"
        "1. /explore → 1 (Battle) to fight NPCs.\n"
        "2. Every 15 NPC wins = world boss encounter.\n"
        "3. On defeat, HP drops to 1. Use /heal → Senzu Bean.\n"
        "4. HP persists across battles until healed.\n\n"
        "<b>OP Commands</b>\n"
        "• /duel <@user> — PvP battle!\n"
        "• /bounty — High-risk boss for 10x rewards.\n"
        "• /ultimate — Massive attack costing SP.\n\n"
        "<b>Progression</b>\n"
        "5. /rankup multiplies all stats by +15% per rank.\n"
        "6. Travel to higher planets for bigger rewards.\n"
        "7. Complete /quests for daily/weekly/monthly bonuses.",
        parse_mode=ParseMode.HTML
    )

# =============================================================
# PROFILE / TEAM
# =============================================================
@require_not_banned
async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return
    s = player_stats(uid)
    stats = db.combat_stats(uid)
    clan = clans.membership(uid)
    bar = _hp_bar(s["current_hp"], s["max_hp"])
    world = p["travel"] or "Earth"
    world_mult = get_world_multiplier(uid)

    text = (
        f"👤 <b>{escape(p['first_name'])}</b>\n"
        f"🆔 <code>{uid}</code>\n"
        f"Level: <b>{s['level']}</b>  Rank: <b>{s['rank']}</b> ({G.RANKS.get(s['rank'], '?')})\n"
        f"World: {world} (x{world_mult})\n\n"
        f"❤️ HP: {s['current_hp']}/{s['max_hp']}\n   {bar}\n"
        f"💪 ATK: <b>{s['atk']}</b>\n⚡ SPD: {s['spd']}\n🎯 CRIT: {s['crit']}%\n\n"
        f"💴 Yens: {p['yens']:,}\n💎 Gems: {p['gems']:,}\n🔮 Crystals: {p['crystals']:,}\n"
        f"🎫 Tickets: {p['tickets']}\n🌟 Skill Points: {p['skill_points']}\n"
        f"🔑 Elite Keys: {p['keys_elite']}\n🗝️ Boss Keys: {p['keys_boss']}\n\n"
        f"⚔️ Weapon: {escape(p['weapon'] or 'none')}\n"
        f"🛡️ Armor: {escape(p['armor'] or 'none')}\n"
        f"🧙 Character: {escape(p['equipped_char'] or 'none')}\n\n"
        f"📊 Battles: {stats['battles']}\n   {stats['wins']}W / {stats['losses']}L\n"
        f"🩸 Dealt: {stats['dealt']:,}\n🩹 Taken: {stats['taken']:,}\n\n"
        f"🏰 Clan: {escape(clan['name']) if clan else 'none'}\n✨ EXP: {p['exp']}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return
    s = player_stats(uid)
    bar = _hp_bar(s["current_hp"], s["max_hp"])
    await update.message.reply_text(
        f"🛡️ <b>Team Setup</b>\n\n"
        f"Character: {escape(p['equipped_char'] or 'none')}\n"
        f"Weapon: {escape(p['weapon'] or 'none')}\n"
        f"Armor: {escape(p['armor'] or 'none')}\n\n"
        f"❤️ HP: {s['current_hp']}/{s['max_hp']}\n   {bar}\n"
        f"💪 ATK: {s['atk']}\n⚡ SPD: {s['spd']}\n🎯 CRIT: {s['crit']}%\n"
        f"⭐ Rank: {s['rank']} ({G.RANKS.get(s['rank'], '?')})\n\n"
        f"Use /equip &lt;name&gt; to change equipment.\nUse /mycharacters to see your roster.",
        parse_mode=ParseMode.HTML
    )

# =============================================================
# EXPLORE / COMBAT
# =============================================================
@require_not_banned
async def cmd_explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return
    EXPLORE_STATES[uid] = {"mode": "explore"}
    world = p["travel"] or "Earth"
    mult = get_world_multiplier(uid)
    streak = int(p["npc_streak"] or 0)
    to_boss = G.NPC_STREAK_FOR_BOSS - streak
    s = player_stats(uid)
    bar = _hp_bar(s["current_hp"], s["max_hp"])

    keyboard = [
        [InlineKeyboardButton("⚔️ Battle", callback_data="explore_battle")],
        [InlineKeyboardButton("🏃 Run away", callback_data="explore_run")],
        [InlineKeyboardButton("ℹ️ Info", callback_data="explore_info")]
    ]
    await update.message.reply_text(
        f"🧭 <b>Explore — {escape(world)}</b> (x{mult} rewards)\n\n"
        f"❤️ HP: {s['current_hp']}/{s['max_hp']}\n   {bar}\n"
        f"👹 Boss in {to_boss} more NPC wins\n\n"
        f"Choose an action:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

async def handle_explore_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None: return
    uid = update.effective_user.id
    if uid not in EXPLORE_STATES: return
    choice = (update.message.text or "").strip()
    if choice not in {"1", "2", "3"}: return
    
    EXPLORE_STATES.pop(uid, None)
    await _process_explore_choice(update, uid, choice)

async def _process_explore_choice(update: Update, uid: int, choice: str):
    p = db.player(uid)
    if not p: return
    ensure_player_for_update(update)

    if choice == "3":
        world = p["travel"] or "Earth"
        data = G.PLANETS[world]
        streak = int(p["npc_streak"] or 0)
        to_boss = G.NPC_STREAK_FOR_BOSS - streak
        s = player_stats(uid)
        bar = _hp_bar(s["current_hp"], s["max_hp"])
        await update.message.reply_text(
            f"ℹ️ <b>Info: {escape(world)}</b>\n\n"
            f"Level gate: {data['level_min']} – {data['level_max']}\n"
            f"Reward multiplier: x{data['multiplier']}\n"
            f"Lore: {data['desc']}\n\n"
            f"❤️ Your HP: {s['current_hp']}/{s['max_hp']}\n   {bar}\n"
            f"👹 Boss in {to_boss} NPC wins.\n\n"
            f"Drop rates:\n• Equipment: 15%\n• Dragon Ball: 10%\n• Ticket: 5%",
            parse_mode=ParseMode.HTML
        )
        return

    if choice == "2":
        await update.message.reply_text("🏃 You used Instant Transmission and escaped safely.\nNo HP lost. No streak reset.")
        return

    s = player_stats(uid)
    if s["current_hp"] <= 1:
        await update.message.reply_text("⚠️ <b>HP Critical!</b>\n\nYour HP is 1. Use <code>/heal Senzu Bean</code> to restore.", parse_mode=ParseMode.HTML)
        return

    streak = int(p["npc_streak"] or 0) + 1
    is_boss = streak >= G.NPC_STREAK_FOR_BOSS
    world_mult = get_world_multiplier(uid)
    rank_mult = 1 + int(p["rank"] or 1) * 0.2

    if is_boss:
        boss = random.choice(G.BOSSES)
        result = run_battle(uid, boss["name"], boss["hp"], boss["dmg"], enrage=True, is_boss=True)
        if result["won"]:
            yens = int(boss["yens"] * world_mult * rank_mult * 5)
            db.give(uid, yens=yens, gems=boss["gems"], crystals=boss["crystals"], tickets=boss["tickets"], skill_points=boss["skill_points"], exp=200)
            drops = apply_drops(uid, is_boss=True)
            db.set_field(uid, "npc_streak", 0)
            
            ach = combat.CombatAchievements(uid); ach.check_after_battle(True, result["taken"], is_boss=True)
            quest = combat.QuestTracker(uid); quest.on_boss_kill(); quest.on_yens_earned(yens); quest.on_battle_win()
            
            s2 = player_stats(uid); bar = _hp_bar(s2["current_hp"], s2["max_hp"])
            text = f"🏆 <b>BOSS VICTORY: {escape(boss['name'])}</b>\n\n💴 +{yens:,} yens\n💎 +{boss['gems']} gems\n🔮 +{boss['crystals']} crystals\n🎫 +{boss['tickets']} tickets\n🌟 +{boss['skill_points']} SP\n✨ +200 exp\n"
            if drops: text += f"\n<b>Drops:</b>\n" + "\n".join(f"• {d}" for d in drops)
            text += f"\n\n⚔️ Damage: {result['dealt']:,} dealt / {result['taken']:,} taken\n❤️ HP: {s2['current_hp']}/{s2['max_hp']}\n   {bar}\n"
            text += ach.reward_text()
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"❌ <b>Defeat by {escape(boss['name'])}!</b>\n\n❤️ HP restored to 1.\nUse /heal → Senzu Bean to recover.\n\n⚔️ Damage: {result['dealt']:,} dealt / {result['taken']:,} taken\nBoss stays at full HP for next attempt.", parse_mode=ParseMode.HTML)
        return

    npc = random.choice(G.NPCS)
    result = run_battle(uid, npc["name"], npc["hp"], npc["dmg"])
    if result["won"]:
        yens = int(npc["reward"] * world_mult * rank_mult)
        db.give(uid, yens=yens, exp=60, skill_points=5)
        drops = apply_drops(uid, is_boss=False)
        db.set_field(uid, "npc_streak", streak)
        
        ach = combat.CombatAchievements(uid); ach.check_after_battle(True, result["taken"])
        quest = combat.QuestTracker(uid); quest.on_npc_kill(); quest.on_battle_win(); quest.on_yens_earned(yens)
        
        s2 = player_stats(uid); bar = _hp_bar(s2["current_hp"], s2["max_hp"]); to_next = G.NPC_STREAK_FOR_BOSS - streak
        text = f"✅ <b>Victory vs {escape(npc['name'])}!</b>\n\n💴 +{yens:,} yens\n✨ +60 exp\n🌟 +5 SP\n"
        if drops: text += f"\n<b>Drops:</b>\n" + "\n".join(f"• {d}" for d in drops)
        text += f"\n\n⚔️ Damage: {result['dealt']:,} dealt / {result['taken']:,} taken\n❤️ HP: {s2['current_hp']}/{s2['max_hp']}\n   {bar}\n👹 Boss in {to_next} more wins\n"
        text += ach.reward_text()
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"❌ <b>Defeat by {escape(npc['name'])}!</b>\n\n❤️ HP restored to 1.\nUse /heal → Senzu Bean to recover.\n\n⚔️ Damage: {result['dealt']:,} dealt / {result['taken']:,} taken", parse_mode=ParseMode.HTML)

# =============================================================
# NEW OP COMMANDS (Bounty, Ultimate, Duel)
# =============================================================
@require_not_banned
async def cmd_bounty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """High-risk, high-reward 1v1 fight."""
    ensure_player_for_update(update)
    uid = update.effective_user.id
    s = player_stats(uid)
    
    if s["current_hp"] <= (s["max_hp"] * 0.5):
        await update.message.reply_text("⚠️ <b>You must have at least 50% HP to attempt a Bounty!</b>\n\nUse /heal to recover first.", parse_mode=ParseMode.HTML)
        return

    boss_hp = int(s["max_hp"] * 1.5)
    boss_dmg = int(s["atk"] * 0.8)

    await update.message.reply_text("🎯 <b>Bounty Accepted!</b>\n\nA heavily armed mercenary is tracking you. Prepare for a brutal fight...", parse_mode=ParseMode.HTML)
    await asyncio.sleep(1.5)

    result = run_battle(uid, "Bounty Hunter", boss_hp, boss_dmg, enrage=True, is_boss=True)
    
    if result["won"]:
        reward_yens = int(s["atk"] * 100)
        reward_gems = random.randint(50, 150)
        db.give(uid, yens=reward_yens, gems=reward_gems, crystals=10, tickets=5)
        await update.message.reply_text(f"🏆 <b>BOUNTY CLAIMED!</b>\n\nYou defeated the Bounty Hunter!\n\n💴 +{reward_yens:,} yens\n💎 +{reward_gems} gems\n🔮 +10 crystals\n🎫 +5 tickets", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ <b>You were defeated!</b>\n\nThe Bounty Hunter escaped. Your HP is now 1.", parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_ultimate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ultimate attack costing SP."""
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    s = player_stats(uid)

    if p["skill_points"] < 50:
        await update.message.reply_text("⚠️ You need at least 50 SP to use /ultimate.", parse_mode=ParseMode.HTML)
        return

    db.spend(uid, "skill_points", 50)
    await update.message.reply_text("💥 <b>ULTIMATE ATTACK UNLEASHED!</b>\n\nYou channel all your Ki into one devastating blow!", parse_mode=ParseMode.HTML)
    
    result = run_battle(uid, "Training Dummy", 999999, 0, skip_hp=True, is_ultimate=True)
    damage = result["dealt"]
    await update.message.reply_text(f"🎯 You dealt <b>{damage:,}</b> damage! (Consumed 50 SP)", parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_duel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """PvP duel between two players."""
    ensure_player_for_update(update)
    uid = update.effective_user.id
    target_id = resolve_target_id(context.args, update)
    
    if not target_id:
        await update.message.reply_text("Usage: <code>/duel &lt;@username or reply&gt;</code>", parse_mode=ParseMode.HTML)
        return
    if target_id == uid:
        await update.message.reply_text("❌ You cannot duel yourself.", parse_mode=ParseMode.HTML)
        return
        
    p1 = db.player(uid)
    p2 = db.player(target_id)
    if not p2:
        await update.message.reply_text("❌ Target player not found.", parse_mode=ParseMode.HTML)
        return

    s1 = player_stats(uid)
    s2 = player_stats(target_id)

    # Simulate PvP
    p1_hp = s1["max_hp"]
    p2_hp = s2["max_hp"]
    
    await update.message.reply_text(f"⚔️ <b>DUEL: {escape(p1['first_name'])} vs {escape(p2['first_name'])}</b>\n\nFIGHT!", parse_mode=ParseMode.HTML)
    await asyncio.sleep(1)

    # Simple turn-based PvP
    round_num = 1
    log = []
    while p1_hp > 0 and p2_hp > 0 and round_num <= 20:
        # P1 Attacks
        hit1 = int(s1["atk"] * random.uniform(0.8, 1.2))
        if random.randint(1, 100) <= s1["crit"]: hit1 = int(hit1 * 1.5)
        p2_hp -= hit1
        log.append(f"Round {round_num}: {escape(p1['first_name'])} hits for {hit1}!")
        if p2_hp <= 0: break
        
        # P2 Attacks
        hit2 = int(s2["atk"] * random.uniform(0.8, 1.2))
        if random.randint(1, 100) <= s2["crit"]: hit2 = int(hit2 * 1.5)
        p1_hp -= hit2
        log.append(f"Round {round_num}: {escape(p2['first_name'])} hits for {hit2}!")
        if p1_hp <= 0: break
        
        round_num += 1

    if p1_hp > p2_hp:
        winner = p1['first_name']
        db.give(uid, yens=5000, gems=50)
        db.set_current_hp(target_id, 1)
        await update.message.reply_text(f"🏆 <b>{escape(winner)} WINS!</b>\n\n" + "\n".join(log[-3:]) + "\n\n💴 +5,000 yens\n💎 +50 gems", parse_mode=ParseMode.HTML)
    else:
        winner = p2['first_name']
        db.give(target_id, yens=5000, gems=50)
        db.set_current_hp(uid, 1)
        await update.message.reply_text(f"🏆 <b>{escape(winner)} WINS!</b>\n\n" + "\n".join(log[-3:]) + "\n\n💴 +5,000 yens (to winner)\n💎 +50 gems", parse_mode=ParseMode.HTML)

# =============================================================
# HEAL / HISTORY
# =============================================================
@require_not_banned
async def cmd_heal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    s = player_stats(uid)
    inv = {row["item"]: row["qty"] for row in db.items(uid)}
    bar = _hp_bar(s["current_hp"], s["max_hp"])

    if not context.args:
        lines = [f"❤️ <b>HP:</b> {s['current_hp']}/{s['max_hp']}", f"   {bar}\n", "🧪 <b>Healing Items in Bag:</b>"]
        any_items = False
        for name, frac in G.HEALING.items():
            qty = inv.get(name, 0)
            if qty > 0:
                any_items = True
                lines.append(f"• {name} ×{qty} — restores {int(frac * 100)}% HP")
        if not any_items:
            lines.append("• <i>You have no healing items.</i>")
            lines.append("\n💡 Buy some with /shop → Healing")
        lines.append("\nUsage: <code>/heal Senzu Bean</code>")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    item_name = " ".join(context.args).strip()
    matched = None
    for name in G.HEALING:
        if name.lower() == item_name.lower(): matched = name; break

    if matched is None:
        await update.message.reply_text(f"❓ Unknown healing item: <b>{escape(item_name)}</b>\nTry: /heal Senzu Bean", parse_mode=ParseMode.HTML)
        return

    ok, new_hp = db.apply_heal(uid, s["max_hp"], matched)
    if not ok:
        await update.message.reply_text(f"❌ You don't have <b>{escape(matched)}</b> in your bag.", parse_mode=ParseMode.HTML)
        return

    bar2 = _hp_bar(new_hp, s["max_hp"])
    await update.message.reply_text(f"💚 <b>Used {escape(matched)}!</b>\n\n❤️ HP: {new_hp}/{s['max_hp']}\n   {bar2}", parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_combathistory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    hist = combat.CombatHistory(uid)
    rows = hist.recent()
    if not rows:
        await update.message.reply_text("📜 <b>Combat History</b>\n\nNo battles recorded yet.\nUse /explore → 1 (Battle) to start fighting.", parse_mode=ParseMode.HTML)
        return

    stats = db.combat_stats(uid)
    lines = ["📜 <b>Combat History — Last 10 Battles</b>\n"]
    for i, row in enumerate(rows, 1):
        icon = "✅" if row["outcome"] == "victory" else "❌"
        lines.append(f"{i}. {icon} vs {escape(row['enemy_name'])}\n   ⚔️ {row['damage_dealt']:,} dealt · {row['damage_taken']:,} taken\n   🔄 {row['rounds']} rounds")
        if row["rewards_text"]: lines.append(f"   🎁 {escape(row['rewards_text'])}")
    lines.append("\n" + "─" * 20 + f"\n📊 <b>Overall Record</b>\nBattles: {stats['battles']}\nWins: {stats['wins']}\nLosses: {stats['losses']}\nDamage dealt: {stats['dealt']:,}\nDamage taken: {stats['taken']:,}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

# =============================================================
# ACHIEVEMENTS / QUESTS
# =============================================================
@require_not_banned
async def cmd_achievements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    unlocked = db.player_achievements(uid)
    have = {row["code"] for row in unlocked}
    stats = db.combat_stats(uid)
    lines = ["🏆 <b>Achievements</b>\n", f"Unlocked: {len(have)} / {len(G.ACHIEVEMENTS)}\n"]
    categories = {
        "Combat": ["first_blood", "battle_10", "battle_50", "battle_100", "battle_500", "flawless", "explore_vet"],
        "Bosses": ["boss_slayer", "summon_hunter", "raid_warrior", "clan_raid_clear"],
        "Clans": ["clan_founder", "clan_leader"],
        "Collection": ["dragon_collector", "mythical_owner", "omni_owner"],
        "Wealth": ["rich", "gem_hoarder"],
        "Progression": ["rank_5", "rank_10", "fusion_master", "traveler"],
    }
    for cat, codes in categories.items():
        lines.append(f"\n<b>{cat}</b>")
        for code in codes:
            if code not in G.ACHIEVEMENTS: continue
            info = G.ACHIEVEMENTS[code]
            icon = "✅" if code in have else "▫️"
            lines.append(f"{icon} {info['name']} — {info['desc']}")
    lines.append(f"\n📊 Your stats: {stats['battles']} battles · {stats['wins']} wins")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_quests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    db.ensure_quests_current(uid)
    lines = ["📋 <b>Active Quests</b>\n"]
    def render_group(kind: str, title: str, table: dict) -> list[str]:
        out = [f"<b>{title}</b>"]
        claimed = db.quest_claimed(uid, kind)
        for code, info in table.items():
            cur = db.quest_progress(uid, kind, code); target = info["target"]
            done = cur >= target; claimed_flag = claimed.get(code, 0) == 1; bar = _progress_bar(cur, target)
            icon = "✅" if claimed_flag else ("🎁" if done else "▫️")
            out.append(f"{icon} {info['name']} — {bar}\n   Reward: {info.get('yens', 0):,} yens · {info.get('gems', 0)} gems · {info.get('crystals', 0)} crystals")
            if done and not claimed_flag: out.append(f"   → <code>/claim_{kind} {code}</code>")
        out.append(""); return out
    lines += render_group("daily", "☀️ Daily Quests", G.DAILY_QUESTS)
    lines += render_group("weekly", "📅 Weekly Quests", G.WEEKLY_QUESTS)
    lines += render_group("monthly", "🌙 Monthly Quests", G.MONTHLY_QUESTS)
    lines.append("<i>Quests reset automatically.</i>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_claim_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    if not context.args: return
    info = db.claim_quest(uid, "daily", context.args[0])
    if info: await update.message.reply_text(f"🎁 <b>Quest claimed: {info['name']}</b>\n💴 +{info.get('yens', 0):,} yens\n💎 +{info.get('gems', 0)} gems\n🔮 +{info.get('crystals', 0)} crystals", parse_mode=ParseMode.HTML)
    else: await update.message.reply_text("❌ Quest not complete or already claimed.")

@require_not_banned
async def cmd_claim_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    if not context.args: return
    info = db.claim_quest(uid, "weekly", context.args[0])
    if info: await update.message.reply_text(f"🎁 <b>Quest claimed: {info['name']}</b>\n💴 +{info.get('yens', 0):,} yens\n💎 +{info.get('gems', 0)} gems\n🔮 +{info.get('crystals', 0)} crystals", parse_mode=ParseMode.HTML)
    else: await update.message.reply_text("❌ Quest not complete or already claimed.")

@require_not_banned
async def cmd_claim_monthly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    if not context.args: return
    info = db.claim_quest(uid, "monthly", context.args[0])
    if info: await update.message.reply_text(f"🎁 <b>Quest claimed: {info['name']}</b>\n💴 +{info.get('yens', 0):,} yens\n💎 +{info.get('gems', 0)} gems\n🔮 +{info.get('crystals', 0)} crystals", parse_mode=ParseMode.HTML)
    else: await update.message.reply_text("❌ Quest not complete or already claimed.")

# =============================================================
# CALLBACK QUERY HANDLER (Inline Buttons)
# =============================================================
@handle_errors
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id
    
    class DummyMessage:
        def __init__(self, query): self.query = query
        async def reply_text(self, text, **kwargs): await self.query.edit_message_text(text=text, **kwargs)
    class DummyUpdate:
        def __init__(self, query):
            self.effective_user = query.from_user
            self.message = DummyMessage(query)
    dummy_update = DummyUpdate(query)

    if data == "profile": await cmd_profile(dummy_update, context)
    elif data == "explore": await cmd_explore(dummy_update, context)
    elif data == "explore_battle": await _process_explore_choice(dummy_update, uid, "1")
    elif data == "explore_run": await _process_explore_choice(dummy_update, uid, "2")
    elif data == "explore_info": await _process_explore_choice(dummy_update, uid, "3")
    elif data == "quests": await cmd_quests(dummy_update, context)
    elif data == "achievements": await cmd_achievements(dummy_update, context)
    elif data == "heal": await cmd_heal(dummy_update, context)
    elif data == "help": await cmd_help(dummy_update, context)
    else:
        await query.edit_message_text("⚠️ Feature coming in Part 2!")

# =============================================================
# ERROR HANDLER
# =============================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    logger.error(f"TRACEBACK:\n{''.join(tb_list)}")
    if isinstance(update, Update) and update.effective_message:
        try: await update.effective_message.reply_text("❌ An internal error occurred. Admin has been notified in terminal logs.")
        except Exception: pass

# =============================================================
# END OF PART 1
# =============================================================
# Reply "Part 2" to receive the rest of bot.py (Shop, Buy, Inventory, Gacha, etc.)
