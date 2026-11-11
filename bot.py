import discord
from discord.ext import commands, tasks
import sqlite3
import json
import re
import os
from datetime import datetime, timezone

# ─── Config ───
TOKEN = os.getenv("DISCORD_TOKEN")

# IDs des owners qui reçoivent le récap en MP
OWNER_IDS = [799396818818564136, 977960166945325106]  # Ayman + MZ

# Regex pour détecter les liens Instagram
INSTA_REGEX = re.compile(
    r"https?://(?:www\.)?instagram\.com/[a-zA-Z0-9_.]+/?",
    re.IGNORECASE,
)

# Fichier pour stocker le message du leaderboard
LB_FILE = "leaderboard_msg.json"

def load_lb_info():
    """Charge le channel_id et message_id du leaderboard installé"""
    if os.path.exists(LB_FILE):
        with open(LB_FILE, "r") as f:
            return json.load(f)
    return None

def save_lb_info(channel_id, message_id):
    with open(LB_FILE, "w") as f:
        json.dump({"channel_id": channel_id, "message_id": message_id}, f)

# ─── Base de données SQLite ───
def init_db():
    conn = sqlite3.connect("tracker.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            insta_link TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def normalize_link(link):
    link = link.lower().rstrip("/")
    link = re.sub(r"https?://(www\.)?instagram\.com", "instagram.com", link)
    return link

def is_duplicate(insta_link):
    conn = sqlite3.connect("tracker.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM accounts WHERE insta_link = ?", (normalize_link(insta_link),))
    exists = c.fetchone()[0] > 0
    conn.close()
    return exists

def add_account(user_id, username, insta_link, channel_id):
    clean_link = normalize_link(insta_link)
    if is_duplicate(clean_link):
        return False
    conn = sqlite3.connect("tracker.db")
    c = conn.cursor()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        "INSERT INTO accounts (user_id, username, insta_link, channel_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (str(user_id), username, clean_link, str(channel_id), now),
    )
    conn.commit()
    conn.close()
    return True

def get_today_leaderboard():
    conn = sqlite3.connect("tracker.db")
    c = conn.cursor()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    c.execute("""
        SELECT user_id, username, COUNT(*) as total
        FROM accounts
        WHERE created_at LIKE ?
        GROUP BY user_id
        ORDER BY total DESC
    """, (f"{today}%",))
    rows = c.fetchall()
    conn.close()
    return rows

# ─── Bot Discord ───
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

def build_leaderboard_embed():
    """Construit l'embed du classement du jour (top 10)"""
    rows = get_today_leaderboard()
    now = datetime.now(timezone.utc)
    today = now.strftime("%d/%m/%Y")

    embed = discord.Embed(
        title=f"⚔️ TOP 10 DU JOUR — {today}",
        color=0xE1306C,
    )

    if not rows:
        embed.description = "Personne au classement pour l'instant… Qui va ouvrir le score ? 👀"
        embed.add_field(
            name="",
            value="**📌 Poste tes liens Instagram dans ton salon perso sinon tes comptes seront pas comptés !**",
            inline=False,
        )
        embed.set_footer(text=f"Mis à jour : {now.strftime('%H:%M UTC')}")
        return embed, 0

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    total_all = sum(total for _, _, total in rows)

    # Afficher seulement le top 10
    for i, (user_id, username, total) in enumerate(rows[:10]):
        medal = medals[i] if i < 3 else f"**{i+1}.**"
        lines.append(f"{medal} {username} — **{total}** compte(s)")

    # Message compétitif selon le top 1
    top_name = rows[0][1]
    lines.insert(0, f"🔥 **{top_name}** domine le classement !\n")

    embed.description = "\n".join(lines)
    embed.add_field(
        name="",
        value="**📌 Poste tes liens Instagram dans ton salon perso sinon tes comptes seront pas comptés !**",
        inline=False,
    )
    embed.set_footer(text=f"Total : {total_all} comptes • Mis à jour : {now.strftime('%H:%M UTC')}")
    return embed, total_all

async def update_leaderboard_msg():
    """Met à jour le message du leaderboard installé"""
    info = load_lb_info()
    if not info:
        return

    try:
        channel = bot.get_channel(info["channel_id"])
        if not channel:
            channel = await bot.fetch_channel(info["channel_id"])
        msg = await channel.fetch_message(info["message_id"])
        embed, _ = build_leaderboard_embed()
        await msg.edit(embed=embed)
    except Exception as e:
        print(f"⚠️ Impossible de mettre à jour le leaderboard: {e}")

async def send_dm_to_owners(embed):
    for owner_id in OWNER_IDS:
        try:
            user = await bot.fetch_user(owner_id)
            await user.send(embed=embed)
        except Exception as e:
            print(f"⚠️ Impossible d'envoyer le MP à {owner_id}: {e}")

@tasks.loop(minutes=1)
async def midnight_recap():
    """À minuit UTC : envoie le récap en MP puis reset le leaderboard"""
    now = datetime.now(timezone.utc)
    if now.hour == 0 and now.minute == 0:
        # Récap du jour en MP
        embed, total = build_leaderboard_embed()
        if total > 0:
            embed.title = f"📋 Récap du jour — {now.strftime('%d/%m/%Y')}"
            await send_dm_to_owners(embed)
        # Le leaderboard se reset tout seul (filtre par date)
        # On met à jour le message pour afficher le nouveau jour vide
        await update_leaderboard_msg()

@bot.event
async def on_ready():
    init_db()
    midnight_recap.start()
    print(f"✅ Bot connecté : {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Détecter les liens Instagram (silencieux)
    links = INSTA_REGEX.findall(message.content)
    if links:
        new_count = 0
        for link in links:
            added = add_account(
                user_id=message.author.id,
                username=message.author.display_name,
                insta_link=link,
                channel_id=message.channel.id,
            )
            if added:
                new_count += 1

        # Si au moins un nouveau compte, on met à jour le leaderboard
        if new_count > 0:
            await update_leaderboard_msg()

    await bot.process_commands(message)

@bot.command(name="leaderboard", aliases=["lb", "classement"])
async def leaderboard(ctx):
    """Installe le leaderboard dans ce salon (se met à jour tout seul)"""
    # Supprimer l'ancien message si il existe
    info = load_lb_info()
    if info:
        try:
            old_channel = bot.get_channel(info["channel_id"])
            if old_channel:
                old_msg = await old_channel.fetch_message(info["message_id"])
                await old_msg.delete()
        except:
            pass

    embed, _ = build_leaderboard_embed()
    msg = await ctx.send(embed=embed)

    # Sauvegarder le message pour les mises à jour auto
    save_lb_info(msg.channel.id, msg.id)

    # Supprimer la commande tapée pour garder le salon propre
    try:
        await ctx.message.delete()
    except:
        pass

    # Envoyer en MP aux owners
    await send_dm_to_owners(embed)

@bot.command(name="stats", aliases=["mystats"])
async def stats(ctx, member: discord.Member = None):
    target = member or ctx.author
    conn = sqlite3.connect("tracker.db")
    c = conn.cursor()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM accounts WHERE user_id = ? AND created_at LIKE ?",
              (str(target.id), f"{today}%"))
    count = c.fetchone()[0]
    conn.close()

    embed = discord.Embed(
        title=f"📊 Stats de {target.display_name}",
        description=f"Comptes créés aujourd'hui : **{count}**",
        color=0xE1306C,
    )
    await ctx.send(embed=embed)

@bot.command(name="reset")
@commands.has_permissions(administrator=True)
async def reset(ctx):
    conn = sqlite3.connect("tracker.db")
    c = conn.cursor()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    c.execute("DELETE FROM accounts WHERE created_at LIKE ?", (f"{today}%",))
    conn.commit()
    conn.close()
    await ctx.send("🗑️ Données du jour réinitialisées.")
    await update_leaderboard_msg()

# ─── Lancer le bot ───
if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ DISCORD_TOKEN non défini !")
