from __future__ import annotations

"""
Dragon Ball RPG — Combat System
================================
Handles battle simulations, history, achievements, and quest tracking.
"""

import csv
import io
import math
import random
from datetime import datetime, timezone

import db
import game_data as G

MAX_ROUNDS_DEFAULT = 60
MIN_DAMAGE = 1
ENRAGE_MULTIPLIER = 1.3
ZENKAI_THRESHOLD = 0.20
ZENKAI_ATK_BOOST = 1.35
AFTERIMAGE_BASE_CHANCE = 0.05
AFTERIMAGE_SPEED_FACTOR = 0.005
CRIT_MULTIPLIER = 1.6
KI_MAX = 100
KI_GAIN_PER_HIT = 15
KI_GAIN_PER_TAKEN = 10
BEAM_STRUGGLE_CHANCE = 0.08

def roll_damage(base: int, spread: tuple[float, float] = (0.8, 1.35)) -> int:
    lo, hi = spread
    return max(MIN_DAMAGE, int(base * random.uniform(lo, hi)))

def roll_crit(crit_chance: int) -> bool:
    return random.randint(1, 100) <= max(0, min(100, int(crit_chance)))

def roll_dodge(speed: int) -> bool:
    chance = AFTERIMAGE_BASE_CHANCE + max(0, speed - 20) * AFTERIMAGE_SPEED_FACTOR
    chance = min(0.35, chance)
    return random.random() < chance

def power_level(stats: dict) -> int:
    hp = int(stats.get("hp", 0) or 0)
    atk = int(stats.get("atk", 0) or 0)
    spd = int(stats.get("spd", 10) or 10)
    crit = int(stats.get("crit", 5) or 5)
    return hp + atk * 10 + spd * 50 + crit * 100

class CombatEngine:
    def __init__(self, player_atk: int, player_hp: int, crit: int = 5, speed: int = 10, *, enable_dodge: bool = True, enable_zenkai: bool = True, max_rounds: int = MAX_ROUNDS_DEFAULT):
        self.p_atk = max(1, int(player_atk))
        self.p_hp = max(1, int(player_hp))
        self.crit = max(0, int(crit))
        self.speed = max(1, int(speed))
        self.max_rounds = max(1, int(max_rounds))
        
        self.enable_dodge = enable_dodge
        self.enable_zenkai = enable_zenkai
        
        self.damage_dealt = 0
        self.damage_taken = 0
        self.rounds = 0
        self.log: list[str] = []
        self.crits_landed = 0
        self.dodges_triggered = 0
        self.zenkai_activations = 0
        self.max_hp = self.p_hp

    def _roll_hit(self, base: int, spread: tuple[float, float] = (0.8, 1.35)) -> int:
        hit = roll_damage(base, spread)
        if roll_crit(self.crit):
            hit = int(hit * CRIT_MULTIPLIER)
            self.crits_landed += 1
            self.log.append("  >> CRITICAL HIT!")
        return max(MIN_DAMAGE, hit)

    def _apply_zenkai(self, base_atk: int, current_hp: int) -> int:
        if self.enable_zenkai and current_hp <= int(self.max_hp * ZENKAI_THRESHOLD):
            return int(base_atk * ZENKAI_ATK_BOOST)
        return base_atk

    def fight(self, enemy_name: str, enemy_hp: int, enemy_dmg: int, enrage: bool = False) -> tuple[bool, int, list[str]]:
        p_hp = self.p_hp
        e_hp = int(enemy_hp)
        e_max = int(enemy_hp)
        
        if e_hp <= 0:
            return True, max(1, p_hp), ["Enemy already defeated!"]

        for rnd in range(1, self.max_rounds + 1):
            self.rounds = rnd
            first = self.speed > 25 or random.random() < 0.5

            if first:
                player_atk_effective = self._apply_zenkai(self.p_atk, p_hp)
                if self.enable_dodge and roll_dodge(20):
                    self.log.append(f"Round {rnd}: {enemy_name} afterimage-dodged!")
                else:
                    hit = self._roll_hit(player_atk_effective)
                    e_hp -= hit
                    self.damage_dealt += hit
                    self.log.append(f"Round {rnd}: You hit {enemy_name} for {hit}")
                    if e_hp <= 0:
                        self.log.append("Victory!")
                        return True, max(1, p_hp), self.log

            mult = ENRAGE_MULTIPLIER if (enrage and e_hp <= e_max // 2) else 1.0
            effective_enemy_dmg = max(MIN_DAMAGE, int(enemy_dmg * mult))

            if self.enable_dodge and roll_dodge(self.speed):
                self.dodges_triggered += 1
                self.log.append(f"Round {rnd}: You afterimage-dodged {enemy_name}'s attack!")
            else:
                taken = self._roll_hit(effective_enemy_dmg, (0.8, 1.25))
                p_hp -= taken
                self.damage_taken += taken
                self.log.append(f"Round {rnd}: {enemy_name} hits for {taken}")
                if p_hp <= 0:
                    return False, 0, self.log

            if not first:
                player_atk_effective = self._apply_zenkai(self.p_atk, p_hp)
                if self.enable_dodge and roll_dodge(20):
                    self.log.append(f"Round {rnd}: {enemy_name} afterimage-dodged!")
                else:
                    hit = self._roll_hit(player_atk_effective)
                    e_hp -= hit
                    self.damage_dealt += hit
                    self.log.append(f"Round {rnd}: You hit {enemy_name} for {hit}")
                    if e_hp <= 0:
                        self.log.append("Victory!")
                        return True, max(1, p_hp), self.log

        self.log.append("Battle dragged on and you backed off.")
        return False, max(0, p_hp), self.log

class CombatHistory:
    def __init__(self, uid: int, limit: int = 10):
        self.uid = int(uid)
        self.limit = max(1, int(limit))

    def log(self, enemy_name: str, enemy_max_hp: int, damage_dealt: int, damage_taken: int, outcome: str, rounds: int, rewards_text: str = "") -> None:
        db.add_combat_log(self.uid, enemy_name, enemy_max_hp, damage_dealt, damage_taken, outcome, rounds, rewards_text)

    def recent(self) -> list:
        return db.get_combat_history(self.uid, self.limit)

    def stats(self) -> dict:
        return db.combat_stats(self.uid)

class CombatAchievements:
    def __init__(self, uid: int):
        self.uid = int(uid)
        self.newly_unlocked: list[dict] = []

    def _unlock(self, code: str) -> None:
        info = db.unlock_achievement(self.uid, code)
        if info:
            self.newly_unlocked.append(info)

    def check_after_battle(self, won: bool, damage_taken: int, is_boss: bool = False, is_summon: bool = False) -> None:
        s = db.combat_stats(self.uid)
        if won:
            self._unlock("first_blood")
            if s["battles"] >= 10: self._unlock("battle_10")
            if s["battles"] >= 50: self._unlock("battle_50")
            if s["battles"] >= 100: self._unlock("battle_100")
            if s["battles"] >= 500: self._unlock("battle_500")
            if damage_taken == 0: self._unlock("flawless")
            if is_boss: self._unlock("boss_slayer")
            if is_summon: self._unlock("summon_hunter")
            if s["wins"] >= 100: self._unlock("explore_vet")
            
        p = db.player(self.uid)
        if p:
            if p["yens"] >= 1_000_000: self._unlock("rich")
            if p["gems"] >= 1_000: self._unlock("gem_hoarder")
            if int(p["rank"] or 1) >= 5: self._unlock("rank_5")
            if int(p["rank"] or 1) >= 10: self._unlock("rank_10")
            if p["fusion_count"]: self._unlock("fusion_master")
            if (p["travel"] or "Earth") == "Omniverse": self._unlock("traveler")

    def check_dragon_balls(self) -> None:
        have = db.dragon_balls(self.uid)
        if all(have.get(s, 0) >= 1 for s in G.DRAGON_BALL_STARS):
            self._unlock("dragon_collector")

    def check_character(self, rarity: str) -> None:
        if rarity == "mythical": self._unlock("mythical_owner")
        elif rarity == "omni": self._unlock("omni_owner")

    def check_clan(self, leader: bool = False) -> None:
        self._unlock("clan_founder")
        if leader: self._unlock("clan_leader")

    def check_raid_cleared(self) -> None:
        self._unlock("raid_warrior")

    def reward_text(self) -> str:
        if not self.newly_unlocked: return ""
        lines = ["", "🏆 Achievements unlocked:"]
        for info in self.newly_unlocked:
            parts = []
            if info.get("yens"): parts.append(f"{info['yens']:,} yens")
            if info.get("gems"): parts.append(f"{info['gems']} gems")
            if info.get("crystals"): parts.append(f"{info['crystals']} crystals")
            lines.append(f"• {info['name']} — {', '.join(parts) or 'unlocked'}")
        return "\n".join(lines)

class QuestTracker:
    def __init__(self, uid: int):
        self.uid = int(uid)

    def on_npc_kill(self) -> None:
        db.quest_increment(self.uid, "daily", "daily_npc_10")
        db.quest_increment(self.uid, "weekly", "weekly_npc_100")
        db.quest_increment(self.uid, "monthly", "monthly_npc_500")

    def on_battle_win(self) -> None:
        db.quest_increment(self.uid, "daily", "daily_win_5")
        db.quest_increment(self.uid, "monthly", "monthly_win_250")

    def on_boss_kill(self) -> None:
        db.quest_increment(self.uid, "weekly", "weekly_boss_3")

    def on_summon_kill(self) -> None:
        db.quest_increment(self.uid, "monthly", "monthly_summon_5")

    def on_yens_earned(self, amount: int) -> None:
        amount = max(0, int(amount or 0))
        if amount <= 0: return
        db.quest_increment(self.uid, "daily", "daily_earn_5000", amount)
        db.quest_increment(self.uid, "weekly", "weekly_earn_50k", amount)
        db.quest_increment(self.uid, "monthly", "monthly_earn_500k", amount)

    def on_gacha(self, rolls: int) -> None:
        rolls = max(0, int(rolls or 0))
        if rolls <= 0: return
        db.quest_increment(self.uid, "daily", "daily_gacha_1", rolls)
        db.quest_increment(self.uid, "weekly", "weekly_gacha_10", rolls)
