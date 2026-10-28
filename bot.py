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
# =============================================================
# PART 2 — SHOP / BLACK MARKET / BUY / INVENTORY / GACHA / CHARACTERS
# =============================================================

SHOP_CATEGORY_ALIASES = {
    "weapon": "weapon", "weapons": "weapon", "1": "weapon",
    "armor": "armor", "armors": "armor", "armour": "armor",
    "armours": "armor", "2": "armor",
    "crystal": "crystal", "crystals": "crystal", "3": "crystal",
    "heal": "heal", "healing": "heal", "potion": "heal",
    "potions": "heal", "4": "heal",
    "fusion": "fusion", "fusion_material": "fusion",
    "fusion_materials": "fusion", "5": "fusion",
    "ticket": "ticket", "tickets": "ticket", "key": "key",
    "keys": "key", "6": "key",
}

def normalize_shop_category(token: str) -> str | None:
    return SHOP_CATEGORY_ALIASES.get((token or "").strip().lower())

def shop_items_in_category(category: str) -> list[tuple[int, tuple]]:
    results = []
    for sid, item in G.SHOP.items():
        name, price, currency, kind, amount = item
        if category == "key":
            if kind in ("key_elite", "key_boss"): results.append((sid, item))
        elif category == "weapon":
            if kind == "weapon": results.append((sid, item))
        elif category == "armor":
            if kind == "armor": results.append((sid, item))
        elif category == "crystal":
            if kind == "crystal": results.append((sid, item))
        elif category == "heal":
            if kind == "heal": results.append((sid, item))
        elif category == "fusion":
            if kind == "fusion": results.append((sid, item))
        elif category == "ticket":
            if kind == "ticket": results.append((sid, item))
    return results

@require_not_banned
async def cmd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return

    if not context.args:
        lines = [
            "🛒 <b>Capsule Corp Marketplace</b>\n",
            f"💴 {p['yens']:,} yens · 💎 {p['gems']:,} gems · 🔮 {p['crystals']:,} crystals\n",
            "Choose a department:\n",
            "<b>1.</b> ⚔️ Weapons",
            "<b>2.</b> 🛡️ Armors",
            "<b>3.</b> 🔮 Crystals",
            "<b>4.</b> 🧪 Healing Potions & Senzu Beans",
            "<b>5.</b> 🧬 Fusion Materials",
            "<b>6.</b> 🎫 Tickets & Summon Keys\n",
            "🕶️ <b>blackmarket</b> — Secret OP Items (Crystals only)\n",
            "<i>Usage:</i> <code>/shop weapons</code>",
            "<i>Buy:</i> <code>/buy 1 10</code> (item 1, x10)",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    category = normalize_shop_category(context.args[0])
    if category is None:
        await update.message.reply_text("❓ Unknown category.\nTry: weapons, armors, crystals, healing, fusion, tickets", parse_mode=ParseMode.HTML)
        return

    items = shop_items_in_category(category)
    if not items:
        await update.message.reply_text("No items in that category.")
        return

    cat_names = {
        "weapon": "⚔️ Weapons", "armor": "🛡️ Armors", "crystal": "🔮 Crystals",
        "heal": "🧪 Healing", "fusion": "🧬 Fusion Materials",
        "ticket": "🎫 Gacha Tickets", "key": "🗝️ Summon Keys",
    }
    lines = [f"🛒 <b>{cat_names[category]}</b>\n", f"💴 {p['yens']:,} · 💎 {p['gems']:,} · 🔮 {p['crystals']:,}\n"]
    for sid, item in items:
        name, price, currency, kind, amount = item
        cur_icon = {"yens": "💴", "gems": "💎", "crystals": "🔮"}.get(currency, "•")
        qty_text = f" ×{amount}" if amount > 1 else ""
        lines.append(f"<b>#{sid}</b> {escape(name)}{qty_text}\n   {cur_icon} {price:,} {currency}")
    lines.append("\n💡 <b>Buy:</b> <code>/buy &lt;id&gt; [qty]</code>")
    lines.append("<i>Bulk discounts auto-applied for qty ≥ 5.</i>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_blackmarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """NEW OP FEATURE: Black Market (Crystals only)."""
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return

    black_market_items = {
        9001: ("Ultra Instinct Potion", 500, "crystals", "heal", 1),
        9002: ("Zenkai Core", 1500, "crystals", "fusion", 1),
        9003: ("Omni Gacha Ticket", 2000, "crystals", "ticket", 1),
        9004: ("Beerus Armor", 5000, "crystals", "armor", 1),
        9005: ("Grand Priest Staff", 8000, "crystals", "weapon", 1),
    }
    
    lines = [
        "🕶️ <b>Black Market</b>\n",
        "<i>Only the most elite warriors trade here.</i>\n",
        f"🔮 Your Crystals: {p['crystals']:,}\n",
    ]
    
    for sid, item in black_market_items.items():
        name, price, currency, kind, amount = item
        lines.append(f"<b>#{sid}</b> {escape(name)}\n   🔮 {price:,} crystals")
        
    lines.append("\n💡 <b>Buy:</b> <code>/buy &lt;id&gt;</code>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

def _bulk_price(base_price: int, qty: int) -> int:
    total = base_price * qty
    if qty >= 50: total = int(total * 0.85)
    elif qty >= 10: total = int(total * 0.90)
    elif qty >= 5: total = int(total * 0.95)
    return total

@require_not_banned
async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return

    if not context.args:
        await update.message.reply_text("Usage: <code>/buy &lt;item_id&gt; [quantity]</code>\nExample: <code>/buy 5 10</code> — buy item #5 ×10\n\nFirst use <code>/shop</code> to see item IDs.", parse_mode=ParseMode.HTML)
        return

    item_id_str = context.args[0]
    if not item_id_str.isdigit():
        await update.message.reply_text("❌ Item ID must be a number.\nExample: <code>/buy 5 10</code>", parse_mode=ParseMode.HTML)
        return

    sid = int(item_id_str)
    
    # Check Black Market first
    black_market_items = {
        9001: ("Ultra Instinct Potion", 500, "crystals", "heal", 1),
        9002: ("Zenkai Core", 1500, "crystals", "fusion", 1),
        9003: ("Omni Gacha Ticket", 2000, "crystals", "ticket", 1),
        9004: ("Beerus Armor", 5000, "crystals", "armor", 1),
        9005: ("Grand Priest Staff", 8000, "crystals", "weapon", 1),
    }
    item = G.SHOP.get(sid) if sid not in black_market_items else black_market_items[sid]
    
    if item is None:
        await update.message.reply_text(f"❌ No item with ID #{sid}.\nUse /shop to browse items.")
        return

    qty = 1
    if len(context.args) >= 2:
        qty_parsed = parse_amount(context.args[1])
        if qty_parsed <= 0:
            await update.message.reply_text("❌ Quantity must be a positive number.")
            return
        qty = min(qty_parsed, 100)

    name, price, currency, kind, amount = item
    total_price = _bulk_price(price, qty)

    if not db.spend(uid, currency, total_price):
        p = db.player(uid)
        have = p[currency] if p else 0
        await update.message.reply_text(f"❌ <b>Not enough {currency}.</b>\n\nNeed: {total_price:,} {currency}\nHave: {have:,} {currency}\n\n{'💡 Bulk discount applied!' if qty >= 5 else ''}", parse_mode=ParseMode.HTML)
        return

    delivered_text = ""
    total_amount = amount * qty
    if kind == "weapon": db.add_item(uid, name, qty); delivered_text = f"⚔️ {name} ×{qty} added to inventory"
    elif kind == "armor": db.add_item(uid, name, qty); delivered_text = f"🛡️ {name} ×{qty} added to inventory"
    elif kind == "crystal": db.give(uid, crystals=total_amount); delivered_text = f"🔮 +{total_amount} crystals"
    elif kind == "ticket": db.give(uid, tickets=total_amount); delivered_text = f"🎫 +{total_amount} gacha tickets"
    elif kind == "heal": db.add_item(uid, name, qty); delivered_text = f"🧪 {name} ×{qty} added to inventory"
    elif kind == "fusion": db.add_item(uid, name, qty); delivered_text = f"🧬 {name} ×{qty} added to inventory"
    elif kind == "key_elite": db.give(uid, keys_elite=total_amount); delivered_text = f"🔑 +{total_amount} Elite Keys"
    elif kind == "key_boss": db.give(uid, keys_boss=total_amount); delivered_text = f"🗝️ +{total_amount} Boss Keys"
    else: delivered_text = f"✅ {name} ×{qty}"

    discount_text = ""
    if qty >= 50: discount_text = "\n🎉 15% bulk discount applied!"
    elif qty >= 10: discount_text = "\n🎉 10% bulk discount applied!"
    elif qty >= 5: discount_text = "\n🎉 5% bulk discount applied!"

    await update.message.reply_text(f"✅ <b>Purchase Complete</b>\n\n🛒 {escape(name)} ×{qty}\n💴 Paid: {total_price:,} {currency}\n{discount_text}\n\n{delivered_text}", parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    rows = db.items(uid)
    if not rows:
        await update.message.reply_text("🎒 <b>Inventory Empty</b>\n\nBuy items with /shop or earn them from battles.", parse_mode=ParseMode.HTML)
        return

    groups = {"⚔️ Weapons": [], "🛡️ Armors": [], "🧪 Healing": [], "🧬 Fusion": [], "📦 Other": []}
    for row in rows:
        item_name = row["item"]; qty = row["qty"]
        if item_name in G.WEAPONS: groups["⚔️ Weapons"].append((item_name, qty))
        elif item_name in G.ARMORS: groups["🛡️ Armors"].append((item_name, qty))
        elif item_name in G.HEALING: groups["🧪 Healing"].append((item_name, qty))
        elif item_name in G.FUSION_MATERIALS: groups["🧬 Fusion"].append((item_name, qty))
        else: groups["📦 Other"].append((item_name, qty))

    lines = ["🎒 <b>Inventory</b>\n"]
    for header, items in groups.items():
        if not items: continue
        lines.append(f"\n<b>{header}</b>")
        for item_name, qty in items: lines.append(f"• {escape(item_name)} ×{qty}")
    lines.append("\n💡 Use <code>/equip &lt;name&gt;</code> to equip weapons/armors.\nUse <code>/heal &lt;item&gt;</code> to consume potions.")

    text = "\n".join(lines)
    if len(text) < 4000: await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("\n".join(lines[:25]), parse_mode=ParseMode.HTML)
        await update.message.reply_text("\n".join(lines[25:]), parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_gacha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return

    qty = 1; banner_key = "standard"
    if context.args:
        if context.args[0].isdigit():
            qty = min(int(context.args[0]), 50)
            if qty < 1: qty = 1
            if len(context.args) >= 2: banner_key = context.args[1].lower()
        else: banner_key = context.args[0].lower()

    banner = G.GACHA_BANNERS.get(banner_key)
    if banner is None:
        await update.message.reply_text(f"❓ Unknown banner: <b>{escape(banner_key)}</b>\n\nAvailable banners:\n• <code>standard</code> — Normal rates (1 ticket)\n• <code>hero</code> — Boosted Legendary & Mythical (1 ticket)\n• <code>god</code> — Boosted Mythical & Omni (2 tickets)\n• <code>omni</code> — Boosted Omni x3 (3 tickets)", parse_mode=ParseMode.HTML)
        return

    ticket_cost = banner["cost"] * qty
    if p["tickets"] < ticket_cost:
        await update.message.reply_text(f"❌ <b>Not enough tickets.</b>\n\nNeed: {ticket_cost} tickets\nHave: {p['tickets']} tickets\n\n💡 Buy more with <code>/shop tickets</code>", parse_mode=ParseMode.HTML)
        return

    if not db.spend(uid, "tickets", ticket_cost):
        await update.message.reply_text("❌ Failed to spend tickets.")
        return

    rolls = []; rarity_counts = {}
    for _ in range(qty):
        rarity = db.roll_rarity(banner["bonus"])
        name = db.pick_character_of_rarity(rarity)
        if name is None: continue
        db.add_character(uid, name, rarity)
        rolls.append((name, rarity))
        rarity_counts[rarity] = rarity_counts.get(rarity, 0) + 1

    quest = combat.QuestTracker(uid); quest.on_gacha(qty)
    ach = combat.CombatAchievements(uid)
    for name, rarity in rolls: ach.check_character(rarity)

    rarity_icons = {"common": "⚪", "rare": "🔵", "epic": "🟣", "legendary": "🟡", "mythical": "🔴", "omni": "👑"}
    lines = [f"🎰 <b>Gacha Results — {escape(banner['name'])}</b>\n", f"Rolled {qty} × cost {banner['cost']} = -{ticket_cost} tickets\n"]

    if qty <= 20:
        for i, (name, rarity) in enumerate(rolls, 1): lines.append(f"{i}. {rarity_icons.get(rarity, '•')} <b>{escape(name)}</b> ({rarity.capitalize()})")
    else:
        lines.append("<b>Summary:</b>")
        for rarity in ["omni", "mythical", "legendary", "epic", "rare", "common"]:
            count = rarity_counts.get(rarity, 0)
            if count: lines.append(f"{rarity_icons.get(rarity, '•')} {rarity.capitalize()}: {count}")
        high_value = [(n, r) for n, r in rolls if r in ("omni", "mythical", "legendary")]
        if high_value:
            lines.append("\n<b>High-value pulls:</b>")
            for name, rarity in high_value: lines.append(f"{rarity_icons.get(rarity, '•')} {escape(name)} ({rarity.capitalize()})")

    lines.append(f"\n💎 Check /mycharacters to see your full roster.")
    lines.append(ach.reward_text())
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_mycharacters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return

    groups = db.characters_by_rarity(uid)
    total = sum(len(v) for v in groups.values())
    if total == 0:
        await update.message.reply_text("🧙 <b>Your Warriors</b>\n\nYou have no characters yet.\nRoll the gacha with /gacha to get warriors!", parse_mode=ParseMode.HTML)
        return

    lines = [f"🧙 <b>Your Warriors</b> — {total} total\n", f"Equipped: <b>{escape(p['equipped_char'] or 'none')}</b>\n"]
    rank_order = [("omni", "👑 OMNI KINGS", "═══"), ("mythical", "🔴 MYTHICAL", "───"), ("legendary", "🟡 LEGENDARY", "───"), ("epic", "🟣 EPIC", "───"), ("rare", "🔵 RARE", "───"), ("common", "⚪ COMMON", "───")]

    for rarity, header, divider in rank_order:
        names = groups.get(rarity, [])
        if not names: continue
        lines.append(f"\n<b>{divider} {header} {divider}</b>")
        lines.append(f"<i>{len(names)} warrior{'s' if len(names) != 1 else ''}</i>")
        for name in names:
            marker = " ⭐" if name == p["equipped_char"] else ""
            chp, catk, cspd, ccrit = _char_stats_by_name(name)
            lines.append(f"• <b>{escape(name)}</b>{marker}\n   ❤️{chp} ⚔️{catk} ⚡{cspd} 🎯{ccrit}%")

    lines.append(f"\n💡 <code>/equip &lt;name&gt;</code> to set lead warrior")
    text = "\n".join(lines)
    if len(text) < 4000: await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    else:
        chunk = []; chunk_len = 0
        for line in lines:
            if chunk_len + len(line) > 3800:
                await update.message.reply_text("\n".join(chunk), parse_mode=ParseMode.HTML)
                chunk = []; chunk_len = 0
            chunk.append(line); chunk_len += len(line) + 1
        if chunk: await update.message.reply_text("\n".join(chunk), parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_equip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return

    if not context.args:
        await update.message.reply_text(f"⚔️ <b>Equip</b>\n\nCurrent weapon: {escape(p['weapon'] or 'none')}\nCurrent armor: {escape(p['armor'] or 'none')}\nCurrent character: {escape(p['equipped_char'] or 'none')}\n\nUsage: <code>/equip &lt;name&gt;</code>\nExample: <code>/equip Z Sword</code>\nExample: <code>/equip Ultra Instinct Goku</code>", parse_mode=ParseMode.HTML)
        return

    name = " ".join(context.args).strip()
    if name in G.EQUIPMENT:
        owned = {row["item"] for row in db.items(uid)}
        if name not in owned:
            await update.message.reply_text(f"❌ You don't own <b>{escape(name)}</b>.\nBuy it with /shop or get it from battles.", parse_mode=ParseMode.HTML)
            return
        slot = G.EQUIPMENT[name][0]
        old = p[slot] if p else None
        db.set_field(uid, slot, name)
        slot_icon = "⚔️" if slot == "weapon" else "🛡️"
        old_text = f"\n<i>Replaced: {escape(old)}</i>" if old else ""
        await update.message.reply_text(f"{slot_icon} <b>Equipped {escape(name)}</b>{old_text}", parse_mode=ParseMode.HTML)
        return

    if db.has_character(uid, name):
        old = p["equipped_char"] if p else None
        db.set_field(uid, "equipped_char", name)
        old_text = f"\n<i>Replaced: {escape(old)}</i>" if old else ""
        rarity = "common"
        for cname, crarity in db.characters(uid):
            if cname == name: rarity = crarity; break
        await update.message.reply_text(f"🧙 <b>Equipped {escape(name)}</b> ({rarity.capitalize()}){old_text}\n\nUse /team to see updated stats.", parse_mode=ParseMode.HTML)
        return

    await update.message.reply_text(f"❌ You don't own <b>{escape(name)}</b> (not in inventory or character roster).\n\n• Check /inventory for gear\n• Check /mycharacters for warriors", parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return

    learned = set(db.skills(uid))
    sp = p["skill_points"]
    lines = [f"🌟 <b>Ki Dojo — Skills</b>\n", f"Your SP: <b>{sp}</b>\n", f"Learned: {len(learned)} / {len(G.SKILLS)}\n"]

    if learned:
        lines.append("\n<b>✅ Learned:</b>")
        for skill_name in sorted(learned): lines.append(f"• {skill_name} (+{G.SKILL_ATK.get(skill_name, 0)} ATK)")

    available = [(name, cost) for name, cost in G.SKILLS.items() if name not in learned]
    if available:
        lines.append("\n<b>📖 Available to Learn:</b>")
        for name, cost in sorted(available, key=lambda x: x[1]):
            atk = G.SKILL_ATK.get(name, 0)
            can_learn = "✨" if sp >= cost else "🔒"
            lines.append(f"{can_learn} <b>{escape(name)}</b>\n   {cost} SP → +{atk} ATK")

    lines.append("\n💡 <code>/learn &lt;skill name&gt;</code> to learn")
    text = "\n".join(lines)
    if len(text) < 4000: await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("\n".join(lines[:25]), parse_mode=ParseMode.HTML)
        await update.message.reply_text("\n".join(lines[25:]), parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return

    if not context.args:
        await update.message.reply_text("Usage: <code>/learn &lt;skill name&gt;</code>\nExample: <code>/learn Kaioken</code>\n\nUse /skills to see the full list.", parse_mode=ParseMode.HTML)
        return

    skill_name = " ".join(context.args).strip()
    matched = None
    for name in G.SKILLS:
        if name.lower() == skill_name.lower(): matched = name; break

    if matched is None:
        await update.message.reply_text(f"❓ Unknown skill: <b>{escape(skill_name)}</b>\nUse /skills to see the full list.", parse_mode=ParseMode.HTML)
        return

    if matched in db.skills(uid):
        await update.message.reply_text(f"✅ You already know <b>{escape(matched)}</b>.", parse_mode=ParseMode.HTML)
        return

    cost = G.SKILLS[matched]
    if p["skill_points"] < cost:
        await update.message.reply_text(f"❌ <b>Not enough SP.</b>\n\n<b>{escape(matched)}</b> costs {cost} SP.\nYou have {p['skill_points']} SP.\n\n💡 Earn SP from battles and daily rewards.", parse_mode=ParseMode.HTML)
        return

    if not db.spend(uid, "skill_points", cost):
        await update.message.reply_text("❌ Failed to spend SP.")
        return

    db.add_skill(uid, matched)
    atk_bonus = G.SKILL_ATK.get(matched, 0)
    new_sp = p["skill_points"] - cost
    await update.message.reply_text(f"🌟 <b>Skill Learned: {escape(matched)}</b>\n\n⚔️ +{atk_bonus} ATK added permanently\n🌟 SP remaining: {new_sp}\n\n<i>Your attacks now hit harder in battle!</i>", parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_wish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return

    have = db.dragon_balls(uid)
    missing = [s for s in G.DRAGON_BALL_STARS if have.get(s, 0) < 1]
    ball_line = [f"{s}★" if have.get(s, 0) >= 1 else "▫️" for s in G.DRAGON_BALL_STARS]
    balls_text = " ".join(ball_line)

    if missing:
        missing_text = ", ".join(f"{s}★" for s in missing)
        await update.message.reply_text(f"🐉 <b>Shenron Altar</b>\n\nYour dragon balls: {balls_text}\n\n❌ Missing: {missing_text}\n\nYou need all 7 dragon balls to summon Shenron.\n\n💡 Bosses and summons drop dragon balls at higher rates.", parse_mode=ParseMode.HTML)
        return

    if not db.take_one_of_each_dragon_ball(uid):
        await update.message.reply_text("❌ Failed to consume dragon balls.")
        return

    roll = random.random()
    result_text = ""
    if roll < 0.12:
        rarity = "mythical"; names = [n for n, r, *_ in G.CHARACTERS if r == rarity]
        name = random.choice(names); db.add_character(uid, name, rarity)
        result_text = f"🔴 <b>Mythical Character:</b> {escape(name)}"
        ach = combat.CombatAchievements(uid); ach.check_character("mythical")
    elif roll < 0.22:
        rarity = "omni"; names = [n for n, r, *_ in G.CHARACTERS if r == rarity]
        name = random.choice(names); db.add_character(uid, name, rarity)
        result_text = f"👑 <b>OMNI KING:</b> {escape(name)}"
        ach = combat.CombatAchievements(uid); ach.check_character("omni")
    elif roll < 0.40:
        db.add_item(uid, "Deadly Divine Sword"); result_text = "⚔️ <b>Weapon:</b> Deadly Divine Sword"
    elif roll < 0.55:
        skill = random.choice(list(G.SKILLS)); db.add_skill(uid, skill); result_text = f"🌟 <b>Skill:</b> {escape(skill)}"
    elif roll < 0.80:
        db.give(uid, yens=100000, gems=250, crystals=15, skill_points=20)
        result_text = "💴 <b>Treasure:</b> 100,000 yens, 250 gems, 15 crystals, 20 SP"
    else:
        db.give(uid, gems=500, crystals=20); result_text = "💎 <b>Treasure:</b> 500 gems, 20 crystals"

    ach = combat.CombatAchievements(uid); ach.check_dragon_balls()
    await update.message.reply_text(f"🐉 <b>Shenron has awakened!</b>\n\nYour wish is granted:\n\n{result_text}\n\n<i>All 7 dragon balls consumed and scattered across the multiverse.</i>" + ach.reward_text(), parse_mode=ParseMode.HTML)

# =============================================================
# NEW OP COMMAND: /gift
# =============================================================
@require_not_banned
async def cmd_gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """NEW OP FEATURE: Gift items or currency to another player."""
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    
    if len(context.args) < 3:
        await update.message.reply_text("Usage: <code>/gift &lt;@user&gt; &lt;currency|item|char&gt; &lt;amount|name&gt;</code>\nExample: <code>/gift @friend yens 5000</code>\nExample: <code>/gift @friend char Goku</code>", parse_mode=ParseMode.HTML)
        return
        
    target_id = resolve_target_id(context.args[:1], update)
    if not target_id:
        await update.message.reply_text("❌ Target player not found.")
        return
    if target_id == uid:
        await update.message.reply_text("❌ You cannot gift to yourself.")
        return
        
    g_type = context.args[1].lower()
    
    # Gift Currency
    if g_type in ["yens", "gems", "crystals", "tickets", "skill_points"]:
        amt = parse_amount(context.args[2])
        if amt <= 0:
            await update.message.reply_text("❌ Invalid amount.")
            return
        if not db.spend(uid, g_type, amt):
            await update.message.reply_text(f"❌ You don't have enough {g_type}.")
            return
        db.give(target_id, **{g_type: amt})
        await update.message.reply_text(f"🎁 Successfully gifted {amt:,} {g_type} to {target_id}!", parse_mode=ParseMode.HTML)
        
    # Gift Character
    elif g_type in ["char", "character"]:
        char_name = " ".join(context.args[2:])
        if not db.has_character(uid, char_name):
            await update.message.reply_text(f"❌ You don't own <b>{escape(char_name)}</b>.", parse_mode=ParseMode.HTML)
            return
        if db.has_character(target_id, char_name):
            await update.message.reply_text(f"❌ Target already owns <b>{escape(char_name)}</b>.", parse_mode=ParseMode.HTML)
            return
            
        if db.transfer_character(uid, target_id, char_name):
            await update.message.reply_text(f"🎁 Successfully gifted <b>{escape(char_name)}</b> to {target_id}!", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text("❌ Failed to transfer character.")
            
    else:
        await update.message.reply_text("❌ Invalid gift type. Use: yens, gems, crystals, tickets, skill_points, char.")

# =============================================================
# END OF PART 2
# =============================================================
# =============================================================
# PART 3 — SUMMON / DAILY / RANKUP / TRAVEL / FUSION / CLAN / RAID
# =============================================================

@require_not_banned
async def cmd_summon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Key-based boss battle with PERSISTENT HP."""
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return

    if not context.args:
        lines = ["🔱 <b>Summon Gate</b>\n"]
        for key, info in G.SUMMONS.items():
            persisted = db.summon_hp(uid, key)
            hp_text = (f"{persisted:,}/{info['hp']:,} HP remaining" if persisted else "full HP")
            lines.append(f"<b>{key.capitalize()}</b>\n  Boss: {info['name']}\n  Cost: 1 {info['key'].replace('keys_', '').capitalize()} Key\n  Rewards: 5× normal\n  Status: {hp_text}\n  <code>/summon {key}</code>")
        lines.append("\n<i>💡 If defeated, boss HP is preserved — eat a Senzu and try again!</i>")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    kind = context.args[0].lower()
    data = G.SUMMONS.get(kind)
    if data is None:
        await update.message.reply_text("❓ Unknown summon.\nUse: <code>/summon elite</code>, <code>/summon mythical</code>, or <code>/summon legendary</code>", parse_mode=ParseMode.HTML)
        return

    key_col = data["key"]
    if p[key_col] <= 0:
        await update.message.reply_text(f"🔑 <b>No keys!</b>\n\nYou need 1 {key_col.replace('keys_', '').capitalize()} Key.\nBuy with <code>/shop tickets</code> or defeat bosses.", parse_mode=ParseMode.HTML)
        return

    s = player_stats(uid)
    if s["current_hp"] <= 1:
        await update.message.reply_text("⚠️ <b>HP Critical!</b>\n\nYour HP is 1.\nUse <code>/heal Senzu Bean</code> first.", parse_mode=ParseMode.HTML)
        return

    if not db.spend(uid, key_col, 1):
        await update.message.reply_text("❌ Failed to consume key.")
        return

    boss_name = data["name"]
    boss_max_hp = data["hp"]
    persisted_hp = db.summon_hp(uid, kind)
    current_boss_hp = persisted_hp if persisted_hp else boss_max_hp

    result = run_battle(uid, boss_name, current_boss_hp, data["dmg"], enrage=True, is_boss=True, is_summon=True)

    if result["won"]:
        mult = data["reward_mult"]
        yens = 20000 * mult; gems = 50 * mult; crystals = 6 * mult; sp = 20 * mult; exp = 500 * mult
        db.give(uid, yens=yens, gems=gems, crystals=crystals, skill_points=sp, exp=exp)
        drops = apply_drops(uid, is_boss=True, is_summon=True)
        
        if random.random() < 0.6:
            star = random.choice(G.DRAGON_BALL_STARS); db.add_dragon_ball(uid, star); drops.append(f"🐉 Dragon Ball {star}★")
        if random.random() < 0.4:
            skill = random.choice(list(G.SKILLS))
            if db.add_skill(uid, skill): drops.append(f"🌟 Skill: {skill}")

        db.set_summon_hp(uid, kind, None)
        ach = combat.CombatAchievements(uid); ach.check_after_battle(True, result["taken"], is_boss=True, is_summon=True); ach.check_dragon_balls()
        quest = combat.QuestTracker(uid); quest.on_summon_kill(); quest.on_yens_earned(yens)

        s2 = player_stats(uid); bar = _hp_bar(s2["current_hp"], s2["max_hp"])
        lines = [f"🏆 <b>SUMMON VICTORY: {escape(boss_name)}</b>\n", f"💴 +{yens:,} yens", f"💎 +{gems} gems", f"🔮 +{crystals} crystals", f"🎫 +{sp} SP", f"✨ +{exp} exp"]
        if drops: lines.append("\n<b>Bonus drops:</b>\n" + "\n".join(f"• {d}" for d in drops))
        lines.append(f"\n⚔️ {result['dealt']:,} dealt / {result['taken']:,} taken\n❤️ HP: {s2['current_hp']}/{s2['max_hp']}\n   {bar}")
        lines.append(ach.reward_text())
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    else:
        boss_hp_left = max(0, current_boss_hp - result["dealt"])
        if boss_hp_left > 0: db.set_summon_hp(uid, kind, boss_hp_left)
        await update.message.reply_text(f"❌ <b>Defeated by {escape(boss_name)}!</b>\n\n❤️ HP restored to 1. Use /heal Senzu Bean.\n\n⚔️ {result['dealt']:,} dealt / {result['taken']:,} taken\n\n👹 <b>Boss HP preserved:</b> {boss_hp_left:,}/{boss_max_hp:,}\n<i>Heal up and finish it off!</i>", parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    result = db.claim_daily(uid)
    if result is None:
        await update.message.reply_text("📅 <b>Daily already claimed.</b>\n\nCome back tomorrow for your next reward!", parse_mode=ParseMode.HTML)
        return
    streak, rewards = result
    parts = [f"{ {'yens': '💴', 'gems': '💎', 'crystals': '🔮', 'tickets': '🎫', 'skill_points': '🌟'}.get(k, '•') } +{v:,} {k}" for k, v in rewards.items()]
    await update.message.reply_text(f"📅 <b>Daily Reward Claimed!</b>\n\n🔥 Streak: <b>{streak} day{'s' if streak != 1 else ''}</b>\n\n" + "\n".join(parts), parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    result = db.claim_weekly(uid)
    if result is None:
        await update.message.reply_text("📅 <b>Weekly already claimed.</b>\n\nCome back next week!", parse_mode=ParseMode.HTML)
        return
    rewards, star = result
    parts = [f"{ {'yens': '💴', 'gems': '💎', 'crystals': '🔮', 'tickets': '🎫', 'skill_points': '🌟'}.get(k, '•') } +{v:,} {k}" for k, v in rewards.items()]
    await update.message.reply_text(f"📅 <b>Weekly Reward Claimed!</b>\n\n" + "\n".join(parts) + f"\n\n🐉 Bonus: Dragon Ball {star}★", parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_rankup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return

    rank = int(p["rank"] or 1)
    max_rank = max(G.RANKS.keys())
    if rank >= max_rank:
        await update.message.reply_text(f"👑 <b>You are already at the maximum rank!</b>\n\nRank: {max_rank} ({G.RANKS[max_rank]})\nYou've achieved godhood!", parse_mode=ParseMode.HTML)
        return

    cost = G.RANKUP_CRYSTALS.get(rank)
    if cost is None:
        await update.message.reply_text("❌ Rank system not configured for this rank.")
        return

    if p["crystals"] < cost:
        await update.message.reply_text(f"🔮 <b>Not enough crystals.</b>\n\nRank {rank} → {rank + 1}\nCost: <b>{cost:,} crystals</b>\nYou have: {p['crystals']:,} crystals\n\n💡 Earn crystals from bosses and /wish.", parse_mode=ParseMode.HTML)
        return

    if not db.spend(uid, "crystals", cost): return

    new_rank = rank + 1
    db.set_field(uid, "rank", new_rank)
    db.give(uid, yens=250*rank, skill_points=30*rank)
    ach = combat.CombatAchievements(uid); ach.check_after_battle(True, 0)
    await update.message.reply_text(f"🌟 <b>RANK UP!</b>\n\n<b>{G.RANKS[rank]}</b> → <b>{G.RANKS[new_rank]}</b>\n\n🎁 +{250*rank:,} yens\n🌟 +{30*rank} SP\n⚔️ All stats multiplied by +{int(G.RANK_BONUS * 100)}% permanently!\n\n<i>Total rank bonus: +{int((new_rank - 1) * G.RANK_BONUS * 100)}%</i>" + ach.reward_text(), parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_travel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return

    if not context.args:
        current = p["travel"] or "Earth"; level = db.level_of(p["exp"])
        lines = [f"🌍 <b>Travel — 6 Planet Realms</b>\n", f"You are on: <b>{escape(current)}</b>\n", f"Level: {level}\n"]
        for i, name in enumerate(G.PLANET_ORDER, 1):
            data = G.PLANETS[name]; locked = level < data["level_min"]
            marker = "📍" if name == current else ("🔒" if locked else "▫️")
            lines.append(f"{marker} <b>{i}. {name}</b>\n   Lv {data['level_min']}–{data['level_max']} · x{data['multiplier']} rewards\n   <i>{data['desc']}</i>")
        lines.append("\n💡 <code>/travel &lt;number&gt;</code> to travel")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    idx = int(context.args[0]) if context.args[0].isdigit() else 0
    if idx < 1 or idx > len(G.PLANET_ORDER):
        await update.message.reply_text(f"❌ Planet number must be 1-{len(G.PLANET_ORDER)}.")
        return

    name = G.PLANET_ORDER[idx - 1]; data = G.PLANETS[name]; level = db.level_of(p["exp"])
    if level < data["level_min"]:
        await update.message.reply_text(f"🔒 <b>Level too low for {escape(name)}</b>\n\nRequired: Lv {data['level_min']}\nYour level: {level}\n\n💡 Keep exploring {p['travel'] or 'Earth'} to level up.", parse_mode=ParseMode.HTML)
        return

    db.set_field(uid, "travel", name)
    ach = combat.CombatAchievements(uid); ach.check_after_battle(True, 0)
    await update.message.reply_text(f"🌍 <b>Traveled to {escape(name)}!</b>\n\nReward multiplier: <b>x{data['multiplier']}</b>\n<i>{data['desc']}</i>\n\nUse /explore to start fighting here." + ach.reward_text(), parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_fusion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return

    if not context.args:
        inv = {row["item"]: row["qty"] for row in db.items(uid)}
        mats = [f"• {mat} ×{inv.get(mat, 0)}" for mat in G.FUSION_MATERIALS if inv.get(mat, 0) > 0]
        mat_text = "\n".join(mats) if mats else "<i>None</i>"
        await update.message.reply_text(f"🧬 <b>Metamoran Fusion Chamber</b>\n\nFusion combines two of your warriors into one stronger hybrid (inherits highest rarity).\n\n<b>Materials:</b>\n{mat_text}\n\n<b>Usage:</b>\n<code>/fusion &lt;char1&gt; + &lt;char2&gt;</code>\nExample: <code>/fusion Goku + Vegeta</code>\n\n<b>Rules:</b>\n• Both characters must be in your roster\n• Cannot fuse same character twice\n• Consumes 1 fusion material", parse_mode=ParseMode.HTML)
        return

    raw = " ".join(context.args)
    if "+" in raw:
        parts = raw.split("+", 1); char1 = parts[0].strip(); char2 = parts[1].strip()
    else:
        await update.message.reply_text("❌ Format: <code>/fusion &lt;char1&gt; + &lt;char2&gt;</code>\nExample: <code>/fusion Goku + Vegeta</code>", parse_mode=ParseMode.HTML)
        return

    if not char1 or not char2 or char1.lower() == char2.lower():
        await update.message.reply_text("❌ Both different character names required.")
        return
    if not db.has_character(uid, char1):
        await update.message.reply_text(f"❌ You don't own <b>{escape(char1)}</b>.", parse_mode=ParseMode.HTML)
        return
    if not db.has_character(uid, char2):
        await update.message.reply_text(f"❌ You don't own <b>{escape(char2)}</b>.", parse_mode=ParseMode.HTML)
        return

    inv = {row["item"]: row["qty"] for row in db.items(uid)}
    material = next((mat for mat in G.FUSION_MATERIALS if inv.get(mat, 0) > 0), None)
    if not material:
        await update.message.reply_text("🧬 <b>No fusion material!</b>\n\nBuy Fusion Material with <code>/shop fusion</code>.", parse_mode=ParseMode.HTML)
        return

    db.remove_item(uid, material, 1)
    r1, r2 = "common", "common"
    for cname, crarity in db.characters(uid):
        if cname == char1: r1 = crarity
        if cname == char2: r2 = crarity
    higher_rarity = r1 if G.RARITY_ORDER[r1] >= G.RARITY_ORDER[r2] else r2
    result_name = f"{char1} & {char2}"

    db.add_character(uid, result_name, higher_rarity)
    db.set_field(uid, "fusion_count", int(p["fusion_count"] or 0) + 1)
    ach = combat.CombatAchievements(uid); ach.check_after_battle(True, 0)

    await update.message.reply_text(f"🧬 <b>Fusion Successful!</b>\n\n{escape(char1)} + {escape(char2)}\n→ <b>{escape(result_name)}</b> ({higher_rarity.capitalize()})\n\n<i>Consumed: {material} ×1</i>" + ach.reward_text(), parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_clan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    if not p: return

    if not context.args:
        await update.message.reply_text(f"🏰 <b>Clan Hall</b>\n\n<code>/clan create &lt;name&gt;</code> — 100 crystals\n<code>/clan join &lt;name&gt;</code> — enlist\n<code>/clan info</code> — clan dossier\n<code>/clan members</code> — roster\n<code>/clan manage</code> — officer commands\n<code>/clan treasure</code> — vault\n<code>/clan raid [boss]</code> — raid boss\n<code>/clan announce &lt;text&gt;</code> — bulletin\n<code>/clan invite @user</code> — recruit\n<code>/clan leave</code> — exit clan\n<code>/clan list</code> — top clans\n<code>/clan perks</code> — research tree", parse_mode=ParseMode.HTML)
        return

    action = context.args[0].lower(); sub_args = context.args[1:]

    try:
        if action == "create":
            clans.create(uid, " ".join(sub_args))
            ach = combat.CombatAchievements(uid); ach.check_clan(leader=True)
            await update.message.reply_text(f"🏰 <b>Clan created: {escape(' '.join(sub_args))}</b>\n\nYou are now the leader!\nInvite members with <code>/clan invite @user</code>" + ach.reward_text(), parse_mode=ParseMode.HTML)
        elif action == "join":
            clans.join(uid, " ".join(sub_args))
            await update.message.reply_text(f"🏰 <b>Joined {escape(' '.join(sub_args))}!</b>\n\nUse <code>/clan info</code> to see details.", parse_mode=ParseMode.HTML)
        elif action == "list":
            tops = clans.top_clans(10); lines = ["🏆 <b>Top Clans</b>\n"]
            for i, row in enumerate(tops, 1): lines.append(f"{i}. <b>{escape(row['name'])}</b> — {row['n']} member{'s' if row['n'] != 1 else ''}")
            await update.message.reply_text("\n".join(lines) if tops else "No clans exist yet.", parse_mode=ParseMode.HTML)
        else:
            m = clans.membership(uid)
            if m is None:
                await update.message.reply_text("❌ You are not in a clan.\nUse <code>/clan create &lt;name&gt;</code> or <code>/clan join &lt;name&gt;</code>.", parse_mode=ParseMode.HTML)
                return

            if action == "info":
                await update.message.reply_text(f"🏰 <b>Clan: {escape(m['name'])}</b>\n\nYour role: <b>{m['role'].capitalize()}</b>\nTreasury:\n  💴 {m['treasure_yens']:,} yens\n  💎 {m['treasure_gems']:,} gems\n  🔮 {m['treasure_crystals']:,} crystals\n\n<b>Announcement:</b>\n<i>{escape(m['announcement']) if m['announcement'] else 'No announcement'}</i>", parse_mode=ParseMode.HTML)
            elif action == "members":
                rows = clans.members(m["clan_id"]); lines = [f"🏰 <b>Members of {escape(m['name'])}</b>\n"]
                for r in rows:
                    role_icon = {"leader": "👑", "officer": "⚔️", "member": "•"}.get(r["role"], "•")
                    lines.append(f"{role_icon} {escape(r['first_name'] or 'Unknown')} ({r['role']}) — {r['exp'] or 0:,} exp")
                await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
            elif action == "leave":
                res = clans.leave(uid)
                await update.message.reply_text(f"✅ You left {'and the clan was disbanded' if res == 'disbanded' else 'the clan'}.", parse_mode=ParseMode.HTML)
            elif action in ["promote", "demote", "transfer", "kick", "invite"]:
                target_id = resolve_target_id(sub_args, update)
                if not target_id: await update.message.reply_text("❌ Target user not found."); return
                if action == "promote": clans.promote(uid, target_id)
                elif action == "demote": clans.demote(uid, target_id)
                elif action == "transfer": clans.transfer(uid, target_id)
                elif action == "kick": clans.remove(uid, target_id)
                elif action == "invite": clans.add(uid, target_id)
                await update.message.reply_text(f"✅ Successfully executed /clan {action} on {target_id}.")
            elif action == "treasure":
                await update.message.reply_text(f"🏛️ <b>Clan Vault</b>\n\n💴 Yens: {m['treasure_yens']:,}\n💎 Gems: {m['treasure_gems']:,}\n🔮 Crystals: {m['treasure_crystals']:,}\n\n<code>/clan donate &lt;yens|gems|crystals&gt; &lt;amount&gt;</code>", parse_mode=ParseMode.HTML)
            elif action == "donate":
                col = resource_name(sub_args[0]); amt = parse_amount(sub_args[1])
                if col in ("yens", "gems", "crystals") and amt > 0 and db.spend(uid, col, amt):
                    clans.add_treasure(m["clan_id"], **{col: amt})
                    await update.message.reply_text(f"✅ Donated {amt:,} {col} to clan vault.")
            elif action == "announce":
                if m["role"] in ("leader", "officer"):
                    clans.set_announcement(m["clan_id"], " ".join(sub_args))
                    await update.message.reply_text("📢 Announcement posted.")
            elif action == "raid":
                if sub_args:
                    boss = " ".join(sub_args)
                    if boss in G.CLAN_RAIDS:
                        data = G.CLAN_RAIDS[boss]; clans.clan_raid_start(m["clan_id"], boss, data["hp"], data["dmg"])
                        await update.message.reply_text(f"🔱 <b>Clan Raid Started: {boss}</b>\n\n❤️ HP: {data['hp']:,}\n⚔️ Boss damage: {data['dmg']}\n\n<code>/clan raidhit</code> to attack.", parse_mode=ParseMode.HTML)
                else:
                    state = clans.clan_raid_state(m["clan_id"])
                    if state:
                        pct = int(state["hp"] * 100 / state["max_hp"])
                        await update.message.reply_text(f"🔱 <b>Clan Raid: {state['boss_name']}</b>\n\n❤️ HP: {state['hp']:,}/{state['max_hp']:,} ({pct}%)\n\n<code>/clan raidhit</code> to attack.", parse_mode=ParseMode.HTML)
                    else:
                        await update.message.reply_text("🔱 No active raid. Start one with <code>/clan raid Beerus</code>.", parse_mode=ParseMode.HTML)
            elif action == "raidhit":
                state = clans.clan_raid_state(m["clan_id"])
                if state:
                    s = player_stats(uid); damage = s["atk"] * 3
                    result = clans.clan_raid_hit(m["clan_id"], uid, damage)
                    if result:
                        new_hp, defeated, dmg_by, boss_name = result
                        if defeated:
                            info = G.CLAN_RAIDS.get(boss_name, {})
                            db.give(uid, yens=info.get("reward_yens", 0), gems=info.get("reward_gems", 0), crystals=info.get("reward_crystals", 0))
                            await update.message.reply_text(f"🏆 <b>CLAN RAID CLEARED!</b>\n\n<b>{boss_name}</b> defeated!\nRewards granted.", parse_mode=ParseMode.HTML)
                        else:
                            await update.message.reply_text(f"⚔️ You hit <b>{boss_name}</b> for <b>{damage:,}</b>!\n\n❤️ Boss HP: {new_hp:,}/{state['max_hp']:,}", parse_mode=ParseMode.HTML)
    except clans.ClanError as e:
        await update.message.reply_text(f"❌ {escape(str(e))}", parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.leaderboard(10)
    lines = ["🏆 <b>Leaderboard — Top 10</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(rows, 1):
        medal = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{medal} <b>{escape(row['first_name'] or 'Player')}</b>\n   {row['exp']:,} EXP · Rank {row['rank']} ({G.RANKS.get(int(row['rank'] or 1), '?')})\n   {row['total_wins']} wins")
    await update.message.reply_text("\n".join(lines) if rows else "No players yet.", parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_raid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    st = db.raid_state()
    if not st or not st["active"]:
        await update.message.reply_text("🔱 <b>World Raid</b>\n\n<i>No active raid.</i>\n\nAdmins can start one with <code>/raid_start</code>.", parse_mode=ParseMode.HTML)
        return

    import json
    dmg_by = json.loads(st["damage_json"] or "{}")
    top = sorted(dmg_by.items(), key=lambda x: -x[1])[:3]
    top_lines = [f"{i}. {escape((db.player(int(u)) or {'first_name': u})['first_name'])} — {d:,} dmg" for i, (u, d) in enumerate(top, 1)]
    
    await update.message.reply_text(f"🔱 <b>World Raid: {escape(st['boss_name'] or 'Omega Dragon')}</b>\n\n❤️ HP: {st['hp']:,}/{st['max_hp']:,} ({int(st['hp']*100/st['max_hp'])}%)\n⚔️ Total damage: {sum(dmg_by.values()):,}\n\n<b>Top damage:</b>\n" + "\n".join(top_lines) + "\n\n<code>/raid_attack</code> to hit the boss (0 cooldown!).", parse_mode=ParseMode.HTML)

@require_owner
async def cmd_raid_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.raid_start(boss_name=" ".join(context.args) if context.args else G.RAID["name"])
    await update.message.reply_text(f"🔱 <b>World Raid Started!</b>\n\nAll players can attack with <code>/raid_attack</code>.", parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_raid_attack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_player_for_update(update)
    uid = update.effective_user.id
    st = db.raid_state()
    if not st or not st["active"]: return await update.message.reply_text("❌ No active world raid.")
    
    s = player_stats(uid)
    if s["current_hp"] <= 1: return await update.message.reply_text("⚠️ HP critical! Use /heal first.")

    damage = s["atk"] * 5
    if random.random() < 0.15: damage = int(damage * 1.6)
    result = db.raid_hit(uid, damage)
    if not result: return await update.message.reply_text("❌ Raid ended.")

    new_hp, defeated, _ = result
    if defeated:
        db.give(uid, yens=1000000, gems=5000, crystals=500)
        ach = combat.CombatAchievements(uid); ach.check_raid_cleared()
        await update.message.reply_text(f"🏆 <b>WORLD RAID DEFEATED!</b>\n\nYou dealt the killing blow!\n🎁 +1,000,000 yens, +5,000 gems, +500 crystals" + ach.reward_text(), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"⚔️ You hit for <b>{damage:,}</b>!\n\n❤️ Boss HP: {new_hp:,}/{st['max_hp']:,}", parse_mode=ParseMode.HTML)

# =============================================================
# NEW OP COMMANDS: /autofarm, /dailyclaim, /worldboss
# =============================================================
@require_not_banned
async def cmd_autofarm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """NEW OP FEATURE: Autofarm 10 NPC battles instantly for Gems."""
    ensure_player_for_update(update)
    uid = update.effective_user.id
    p = db.player(uid)
    
    cost = 50
    if p["gems"] < cost:
        await update.message.reply_text(f"💎 You need {cost} Gems to trigger Autofarm.")
        return
        
    s = player_stats(uid)
    if s["current_hp"] < (s["max_hp"] * 0.5):
        await update.message.reply_text("⚠️ You need at least 50% HP to Autofarm.")
        return
        
    db.spend(uid, "gems", cost)
    await update.message.reply_text("🤖 <b>Autofarm initiated...</b>\nFighting 10 NPCs automatically.", parse_mode=ParseMode.HTML)
    await asyncio.sleep(1.5)
    
    total_yens = 0; total_exp = 0; drops = []
    for _ in range(10):
        npc = random.choice(G.NPCS)
        res = run_battle(uid, npc["name"], npc["hp"], npc["dmg"])
        if res["won"]:
            y = int(npc["reward"] * get_world_multiplier(uid))
            total_yens += y; total_exp += 60
            d = apply_drops(uid, False)
            drops.extend(d)
            
    db.give(uid, yens=total_yens, exp=total_exp)
    drop_str = "\n".join(f"• {d}" for d in drops) if drops else "None"
    await update.message.reply_text(f"✅ <b>Autofarm Complete!</b>\n\n💴 +{total_yens:,} yens\n✨ +{total_exp:,} exp\n\n<b>Drops:</b>\n{drop_str}", parse_mode=ParseMode.HTML)

@require_not_banned
async def cmd_dailyclaim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """NEW OP FEATURE: Claim all completed daily quests at once."""
    ensure_player_for_update(update)
    uid = update.effective_user.id
    db.ensure_quests_current(uid)
    
    claimed_any = False
    total_yens = 0; total_gems = 0; total_crystals = 0
    
    for code in G.DAILY_QUESTS.keys():
        info = db.claim_quest(uid, "daily", code)
        if info:
            claimed_any = True
            total_yens += info.get("yens", 0)
            total_gems += info.get("gems", 0)
            total_crystals += info.get("crystals", 0)
            
    if claimed_any:
        await update.message.reply_text(f"🎁 <b>All Daily Quests Claimed!</b>\n\n💴 +{total_yens:,} yens\n💎 +{total_gems} gems\n🔮 +{total_crystals} crystals", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ No daily quests are complete yet.")

@require_not_banned
async def cmd_worldboss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """NEW OP FEATURE: Fight a massive personal boss for high tier gear."""
    ensure_player_for_update(update)
    uid = update.effective_user.id
    s = player_stats(uid)
    
    boss_name = "Legendary Super Saiyan"
    boss_hp = s["atk"] * 8
    boss_dmg = s["max_hp"] // 10
    
    await update.message.reply_text(f"🔥 <b>WORLD BOSS: {boss_name}</b>\n\nA terrifying power level approaches!", parse_mode=ParseMode.HTML)
    await asyncio.sleep(1)
    
    res = run_battle(uid, boss_name, boss_hp, boss_dmg, enrage=True, is_boss=True)
    if res["won"]:
        db.give(uid, crystals=100, tickets=10)
        db.add_item(uid, "Deadly Divine Sword")
        await update.message.reply_text("🏆 <b>WORLD BOSS DEFEATED!</b>\n\n💎 +100 Crystals\n🎫 +10 Tickets\n⚔️ +Deadly Divine Sword", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ You were defeated by the World Boss. Your HP is now 1.", parse_mode=ParseMode.HTML)

# =============================================================
# END OF PART 3
# =============================================================
