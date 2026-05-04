# DEVELOPED BY JOSHSW / KLISM
# WE LOVE TABLE TENNIS!

# Originally designed for Ping Pong Masters Tour
# a local table tennis league in Novi, MI

import os
import discord
import asyncio
from discord import app_commands
from dotenv import load_dotenv
import io
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from elo import *
from utils import is_admin, has_role, format_stats

from typing import Literal, Optional

import doubles_elo as dE
from match_log import (
    can_restore_match,
    find_match,
    get_last_active_match,
    load_match_log,
    mark_match_voided,
    new_match_id,
    record_match,
    restore_before_snapshot,
    snapshot_players,
    utc_now_iso,
)

load_dotenv()

botToken = os.getenv("DISCORD_TOKEN")
ALLOWED_ROLE_IDS = {
    int(x.strip())
    for x in os.getenv("ROLE_ID", "").split(",")
    if x.strip()
}
DEV_GUILD_ID = os.getenv("SERVER_ID")
ELO_RANKS = os.getenv("RANKS", "0") == "1"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return
    if (client.user.mentioned_in(message) and
    message.content.strip() == f"<@{client.user.id}> sync"):
        try:
            synced = await tree.sync()
            await message.channel.send(f"Synced {len(synced)} command(s).")
        except Exception as e:
            await message.channel.send(f"Sync failed: {e}")

from discord.utils import get

@tree.command(name="stats", description="View your or another player's stats")
@app_commands.describe(user="The user to look up (optional)")
async def stats(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    data = load_data()
    register_user(data, user.id)
    user_stats = get_stats(data, user.id)

    def custom(name: str) -> str:
        emoji = get(interaction.guild.emojis, name=name)
        return str(emoji) if emoji else ""

    ranked = sorted(data.items(), key=lambda x: x[1]['elo'], reverse=True)
    idx = next((i for i, (uid, _) in enumerate(ranked) if int(uid) == user.id), None)
    if idx is None:
        return await interaction.response.send_message("Could not find your ranking.")
    rank_number = idx + 1

    user_stats.setdefault('peak_elo', user_stats['elo'])
    elo = user_stats['elo']
    peak = user_stats['peak_elo']

    title = f"{user.display_name} — #{rank_number}"
    embed = discord.Embed(title=title)

    embed.add_field(name="ELO", value=str(elo), inline=True)
    embed.add_field(name="Peak", value=str(peak), inline=True)
    embed.add_field(name="Record", value=f"{user_stats['wins']}W - {user_stats['losses']}L", inline=True)

    embed.add_field(name="Streak", value=str(user_stats['streak']), inline=True)
    embed.add_field(name="All-time Gain", value=str(user_stats.get('all_time_gain', 0)), inline=True)
    embed.add_field(name="All-time Lost", value=str(user_stats.get('all_time_loss', 0)), inline=True)

    neighbor_lines = []
    if idx > 0:
        above_id, above_stats = ranked[idx - 1]
        above_user = await interaction.client.fetch_user(int(above_id))
        neighbor_lines.append(f"{idx}. {above_user.display_name} — {above_stats['elo']}")
    neighbor_lines.append(f"**{rank_number}. {user.display_name} — {elo}**")
    if idx < len(ranked) - 1:
        below_id, below_stats = ranked[idx + 1]
        below_user = await interaction.client.fetch_user(int(below_id))
        neighbor_lines.append(f"{idx + 2}. {below_user.display_name} — {below_stats['elo']}")

    embed.add_field(
        name="Leaderboard",
        value="\n".join(neighbor_lines),
        inline=False
    )

    medals = user_stats.get('medals', [])
    if medals:
        emoji_map = {'gold': '🏆', 'silver': '🥈', 'third': '🥉'}
        medals_text = "\n".join(f"{emoji_map.get(m['medal'], '🏅')} {m['title']}" for m in medals)
    else:
        medals_text = "No medals yet."

    embed.add_field(name="Medals", value=medals_text, inline=False)

    embed.set_thumbnail(url=user.display_avatar.url)

    await interaction.response.send_message(embed=embed)



@tree.command(name="leaderboard", description="View the current top 10 ELO leaderboard")
async def leaderboard(interaction: discord.Interaction):
    data = load_data()
    ranked = sorted(data.items(), key=lambda x: x[1]['elo'], reverse=True)
    msg = "**Leaderboard**\n"
    for i, (uid, stats) in enumerate(ranked[:10], start=1):
        user = await client.fetch_user(int(uid))
        msg += f"{i}. {user.display_name} — {stats['elo']} ELO\n"
    await interaction.response.send_message(msg)

@tree.command(name="match", description="Log a match result (admin only)")
@app_commands.describe(
    winner="Match winner",
    loser="Match loser",
    score_w="Winner's score",
    score_l="Loser's score"
)
async def match(interaction, winner: discord.Member, loser: discord.Member, score_w: int, score_l: int, set_count: Optional[int] = None,
    winner_sets: Optional[str] = None,
    loser_sets: Optional[str] = None,):
    if not (is_admin(interaction.user) or has_role(interaction.user, ALLOWED_ROLE_IDS)):
        return await interaction.response.send_message("No permission", ephemeral=True)

    if winner.id == loser.id:
        return await interaction.response.send_message("Winner and loser must be different players.", ephemeral=True)
    if score_w < 0 or score_l < 0:
        return await interaction.response.send_message("Scores cannot be negative.", ephemeral=True)
    if score_w <= score_l:
        return await interaction.response.send_message("Winner's score must be greater than loser's score.", ephemeral=True)

    data = load_data()
    register_user(data, winner.id)
    register_user(data, loser.id)

    match_id = new_match_id("S")
    logged_at = utc_now_iso()
    before = snapshot_players(data, (winner.id, loser.id))

    result = process_match(
        data,
        winner.id,
        loser.id,
        score_w=score_w,
        score_l=score_l,
        match_id=match_id,
        logged_at=logged_at
    )
    after = snapshot_players(data, (winner.id, loser.id))
    save_data(data)

    record_match(
        match_id=match_id,
        kind="singles",
        guild_id=interaction.guild_id,
        channel_id=interaction.channel_id,
        logged_by_id=interaction.user.id,
        players=(winner.id, loser.id),
        before=before,
        after=after,
        summary={
            "winner_id": str(winner.id),
            "loser_id": str(loser.id),
            "score_w": score_w,
            "score_l": score_l,
            "winner_elo_before": result["winner_elo_before"],
            "loser_elo_before": result["loser_elo_before"],
            "winner_elo_after": result["winner_elo_after"],
            "loser_elo_after": result["loser_elo_after"],
            "elo_gain": result["elo_gain"],
            "elo_loss": result["elo_loss"],
            "bonus": result["bonus"],
            "logged_at": logged_at,
        }
    )

    w_stats = get_stats(data, winner.id)
    l_stats = get_stats(data, loser.id)
    
    set_lines = []
    if set_count and winner_sets and loser_sets:
        w_list = winner_sets.split(",")
        l_list = loser_sets.split(",")
        if len(w_list) == set_count == len(l_list):
            for i in range(set_count):
                set_lines.append(f"> Set {i+1}: {w_list[i].strip()}-{l_list[i].strip()}")

    base  = result['elo_gain']
    bonus = result['bonus']
    bonus_text = f" (+{bonus} bonus)" if bonus else ""
    winner_line = f"> Winner **+{base}**{bonus_text} ({w_stats['wins']}W {w_stats['losses']}L)"
    loser_line  = f"> Loser **-{result['elo_loss']}** ({l_stats['wins']}W {l_stats['losses']}L)"

    header = (
        f"**{winner.mention}** ({result['winner_elo_before']}) "
        f"{score_w} - {score_l} "
        f"**{loser.mention}** ({result['loser_elo_before']})"
    )
    msg = f"`Match ID: {match_id}`\n" + header + "\n"
    if set_lines:
        msg += "\n".join(set_lines) + "\n"
    msg += f"{winner_line}\n{loser_line}"

    rec = w_stats.get('head_to_head', {}).get(str(loser.id), {'wins': 0, 'losses': 0})
    w_new = w_stats['elo']
    l_new = l_stats['elo']
    msg += (
        f"\n> H2H: {winner.mention} (**{w_new}**) "
        f"{rec['wins']}-{rec['losses']} "
        f"{loser.mention} (**{l_new}**)"
    )
    if result['new_streak'] >= 3:
        msg += f"\n> 🔥{result['new_streak']}"

    await interaction.response.send_message(msg)


@tree.command(name="medal", description="Grant a tournament medal to a player (admin)")
@app_commands.describe(user="The player to grant the medal to", medal="Medal type: gold, silver, or third", title="Title of the tournament")
async def medal(interaction: discord.Interaction, user: discord.Member, medal: str, title: str):
    if not is_admin(interaction.user): return await interaction.response.send_message("No permission", ephemeral=True)
    medal = medal.lower()
    if medal not in ["gold","silver","third"]: return await interaction.response.send_message("Medal must be gold, silver, or third.", ephemeral=True)
    emoji_map={'gold':'🥇','silver':'🥈','third':'🥉'}; medal_emoji=emoji_map[medal]
    data=load_data(); register_user(data, user.id)
    data[str(user.id)].setdefault('medals',[]).append({"medal":medal,"title":title}); save_data(data)
    await interaction.response.send_message(f"{medal_emoji} **{user.display_name}** awarded **{medal.upper()}** for *{title}*")

@tree.command(name="h2h", description="View head-to-head record between two players")
@app_commands.describe(player1="First player (mention)", player2="Second player (mention)")
async def h2h(interaction: discord.Interaction, player1: discord.Member, player2: discord.Member):
    if player1.id==player2.id: return await interaction.response.send_message("You must specify two different players.",ephemeral=True)
    data=load_data(); register_user(data, player1.id); register_user(data, player2.id)
    s1=get_stats(data,player1.id); s2=get_stats(data,player2.id)
    e1,e2=s1['elo'],s2['elo']
    ranked=sorted(data.items(),key=lambda x:x[1]['elo'],reverse=True)
    find_rank=lambda uid: next((i+1 for i,(u,_) in enumerate(ranked) if int(u)==uid), '-')
    r1,r2=find_rank(player1.id),find_rank(player2.id)
    rec=s1.get('head_to_head',{}).get(str(player2.id),{'wins':0,'losses':0})
    total=rec['wins']+rec['losses']
    msg=(f"**Head to Head**\n{r1}. {player1.display_name} ({e1}) vs. {r2}. {player2.display_name} ({e2})\n\n"
         f"Games: {total}\n{player2.display_name} {rec['losses']}W - {rec['wins']}W {player1.display_name}")
    await interaction.response.send_message(msg)
    
@tree.command(name="setlosses", description="Set a player's loss count (admin only)")
async def setlosses(interaction: discord.Interaction, user: discord.Member, value: int):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("No permission", ephemeral=True)
    data = load_data()
    register_user(data, user.id)
    set_stat(data, user.id, 'losses', value)
    save_data(data)
    await interaction.response.send_message(f"{user.display_name}'s losses set to {value}.")
    
@tree.command(name="setwins", description="Set a player's win count (admin only)")
async def setwins(interaction: discord.Interaction, user: discord.Member, value: int):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("No permission", ephemeral=True)
    data = load_data()
    register_user(data, user.id)
    set_stat(data, user.id, 'wins', value)
    save_data(data)
    await interaction.response.send_message(f"{user.display_name}'s wins set to {value}.")
    
@tree.command(name="alltime", description="View the top 10 all-time Elo gainers")
async def alltime(interaction: discord.Interaction):
    data = load_data()
    ranked = sorted(
        data.items(),
        key=lambda x: x[1].get('all_time_gain', 0),
        reverse=True
    )
    msg = "**Top ELO Gainers (All Time)**\n"
    for i, (uid, stats) in enumerate(ranked[:10], start=1):
        user = await client.fetch_user(int(uid))
        gain = stats.get('all_time_gain', 0)
        msg += f"{i}. {user.display_name} — {gain}\n"
    await interaction.response.send_message(msg)
    
@tree.command(
    name="simulate",
    description="Simulate an Elo change if player1 beats player2 (no data is modified)"
)
@app_commands.describe(
    player1="Winner in this simulation",
    player2="Loser in this simulation"
)
async def simulate(
    interaction: discord.Interaction,
    player1: discord.Member,
    player2: discord.Member
):
    data = load_data()
    register_user(data, player1.id)
    register_user(data, player2.id)

    p1 = data[str(player1.id)]
    p2 = data[str(player2.id)]
    w_before = p1['elo']
    l_before = p2['elo']
    wins_before = p1['wins']

    p = expected_score(w_before, l_before)

    base_change = math.ceil(K * (1 - p))
    disparity = abs(w_before - l_before)
    capped = max(dISPARITY_MIN, min(disparity, dISPARITY_MAX))
    factor = (capped - dISPARITY_MIN) / (dISPARITY_MAX - dISPARITY_MIN)

    if w_before < l_before:
        win_scale = 1 + factor
        loss_scale = 1
    else:
        win_scale = 1
        loss_scale = 1 - factor

    elo_gain = math.ceil(base_change * win_scale)
    elo_loss = math.ceil(base_change * loss_scale)

    bonus = 5 if wins_before < 5 else 0
    total_gain = elo_gain + bonus

    w_after = max(ELO_FLOOR, w_before + total_gain)
    l_after = max(ELO_FLOOR, l_before - elo_loss)

    await interaction.response.send_message(
        "**SIMULATED MATCH**\n"
        f"{player1.display_name} ({w_before}) beats {player2.display_name} ({l_before})\n\n"
        f"> Winner +{total_gain}\n"
        f"> Loser -{elo_loss}\n\n"
        f"{player1.display_name} ({w_after})\n"
        f"{player2.display_name} ({l_after})\n\n"
        "*This was just a simulation—no real Elo was changed.*"
    )
    
@tree.command(
    name="modifyh2h",
    description="Admin: adjust head-to-head record between two players"
)
@app_commands.describe(
    player1="First player (the POV you’re editing)",
    player2="Second player",
    field="Which H2H field to modify: wins or losses",
    operation="add, subtract, or set",
    amount="How many to apply"
)
async def modifyh2h(
    interaction: discord.Interaction,
    player1: discord.Member,
    player2: discord.Member,
    field: Literal['wins', 'losses'],
    operation: Literal['add', 'subtract', 'set'],
    amount: int
):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("No permission", ephemeral=True)

    data = load_data()
    register_user(data, player1.id)
    register_user(data, player2.id)

    k1, k2 = str(player1.id), str(player2.id)
    h2h1 = data[k1].setdefault('head_to_head', {}).setdefault(k2, {'wins': 0, 'losses': 0})
    h2h2 = data[k2].setdefault('head_to_head', {}).setdefault(k1, {'wins': 0, 'losses': 0})

    old = h2h1[field]
    if operation == 'add':
        new = old + amount
    elif operation == 'subtract':
        new = old - amount
    else:
        new = amount
    new = max(0, new)

    h2h1[field] = new
    inv = 'losses' if field == 'wins' else 'wins'
    h2h2[inv] = new

    save_data(data)
    await interaction.response.send_message(
        f"H2H updated: {player1.display_name} now has {h2h1['wins']}–{h2h1['losses']} vs {player2.display_name}.",
        ephemeral=True
    )
    
@tree.command(
    name="modifyelo",
    description="Admin command"
)
@app_commands.describe(
    user="The player whose ELO to modify",
    field="Which field to modify: current, alltimegain, or alltimeloss",
    operation="add, subtract, or set",
    amount="Amount to apply"
)
async def modifyelo(
    interaction: discord.Interaction,
    user: discord.Member,
    field: Literal['current', 'alltimegain', 'alltimeloss'],
    operation: Literal['add', 'subtract', 'set'],
    amount: int
):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("No permission", ephemeral=True)

    data = load_data()
    register_user(data, user.id)
    entry = data[str(user.id)]

    if field == 'current':
        key = 'elo'
        pretty = 'Current ELO'
    elif field == 'alltimegain':
        key = 'all_time_gain'
        pretty = 'Total ELO Gained'
    else:
        key = 'all_time_loss'
        pretty = 'Total ELO Lost'

    old = entry.get(key, 0)

    if operation == 'add':
        new = old + amount
    elif operation == 'subtract':
        new = old - amount
    else:
        new = amount

    if key == 'elo':
        new = max(ELO_FLOOR, new)
    else:
        new = max(0, new)

    entry[key] = new
    
    entry.setdefault('peak_elo', entry['elo'])
    if entry['elo'] > entry['peak_elo']:
        entry['peak_elo'] = entry['elo']

    save_data(data)

    await interaction.response.send_message(
        f"{user.display_name}'s **{pretty}** has been {operation}ed by {amount}.\n"
        f"Old: {old}, New: {new}."
    )
    
@tree.command(name="losers", description="View the top 10 all-time Elo losers")
async def losers(interaction: discord.Interaction):
    data = load_data()
    ranked = sorted(data.items(), key=lambda x: x[1].get('all_time_loss',0), reverse=True)
    msg = "**Top ELO Losers (All Time)**\n"
    for i,(uid,stats) in enumerate(ranked[:10], start=1):
        loss = stats.get('all_time_loss',0)
        user = await client.fetch_user(int(uid))
        msg += f"{i}. {user.display_name} — {loss}\n"
    await interaction.response.send_message(msg)
    
@tree.command(name="rivals", description="Show the top 10 rivalries by most games played")
async def rivals(interaction: discord.Interaction):
    await interaction.response.defer()

    data = load_data()
    seen = set()
    records = []

    for uid, entry in data.items():
        for opp_id, rec in entry.get('head_to_head', {}).items():
            a, b = sorted((int(uid), int(opp_id)))
            if (a, b) in seen:
                continue
            seen.add((a, b))

            wins_a = data[str(a)]['head_to_head'].get(str(b), {}).get('wins', 0)
            wins_b = data[str(b)]['head_to_head'].get(str(a), {}).get('wins', 0)
            total  = wins_a + wins_b

            records.append(((a, b), wins_a, wins_b, total))

    top = sorted(records, key=lambda x: (x[3], max(x[1], x[2])), reverse=True)[:10]

    lines = ["**Top Rivalries**"]
    for i, ((a, b), wins_a, wins_b, _) in enumerate(top, start=1):
        user_a = await client.fetch_user(a)
        user_b = await client.fetch_user(b)
        elo_a  = data[str(a)]['elo']
        elo_b  = data[str(b)]['elo']

        if wins_a >= wins_b:
            lines.append(
                f"{i}. {user_a.display_name} ({elo_a}) {wins_a}W - {wins_b}W {user_b.display_name} ({elo_b})"
            )
        else:
            lines.append(
                f"{i}. {user_b.display_name} ({elo_b}) {wins_b}W - {wins_a}W {user_a.display_name} ({elo_a})"
            )

    await interaction.followup.send("\n".join(lines))
    
    
@tree.command(name="history", description="Show last 10 matches and Elo trend for a player")
@app_commands.describe(
    user="Player to inspect (defaults to you)"
)
async def history(interaction: discord.Interaction, user: discord.Member = None):
    import io
    import matplotlib.pyplot as plt

    user = user or interaction.user
    data = load_data()
    register_user(data, user.id)
    entry = data[str(user.id)]
    history_list = entry.get("match_history", [])

    if not history_list:
        return await interaction.response.send_message(
            f"No match history for {user.display_name} yet."
        )

    required_keys = {"result", "elo_after", "opponent_elo_after", "opponent_id", "score_w", "score_l"}
    for h in history_list:
        if not required_keys.issubset(h.keys()):
            return await interaction.response.send_message(
                "History contains an entry with an unexpected/old schema. "
                "Please clear or migrate old history entries."
            )

    chronological = list(reversed(history_list))
    elos_after = [h['elo_after'] for h in chronological]

    lines = [f"**Last {len(history_list)} Matches for {user.display_name}**"]
    for h in history_list:
        you_won = (h['result'] == 'W')
        opp_id = h['opponent_id']
        try:
            opp_user = await interaction.client.fetch_user(int(opp_id)) if opp_id else None
        except:
            opp_user = None
        opp_name = opp_user.display_name if opp_user else f"ID:{opp_id}"

        score_w = h.get('score_w')
        score_l = h.get('score_l')
        score_str = f"{score_w}-{score_l}" if (score_w is not None and score_l is not None) else "?"

        if you_won:
            winner_name        = user.display_name
            winner_elo_after   = h['elo_after']
            loser_name         = opp_name
            loser_elo_after    = h['opponent_elo_after']
        else:
            winner_name        = opp_name
            winner_elo_after   = h['opponent_elo_after']
            loser_name         = user.display_name
            loser_elo_after    = h['elo_after']
        outcome_symbol = "🟩" if you_won else "🟥"
        id_text = f" `{h.get('match_id')}`" if h.get('match_id') else ""
        lines.append(
            f"{outcome_symbol}{id_text} {winner_name} ({winner_elo_after}) {score_str} {loser_name} ({loser_elo_after})"
        )

    x_values = list(range(1, len(elos_after) + 1))
    plt.figure()
    plt.plot(x_values, elos_after, marker='o')
    plt.title(f"ELO over last {len(elos_after)} matches")
    plt.xlabel("Match # (oldest → newest)")
    plt.ylabel("ELO")
    plt.grid(True, alpha=0.3)
    
    ax = plt.gca()
    ax.set_xticks(x_values)
    ax.xaxis.set_major_locator(mticker.FixedLocator(x_values))

    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight')
    plt.close()
    buf.seek(0)

    file = discord.File(buf, filename="history.png")
    await interaction.response.send_message("\n".join(lines), file=file)
    
@tree.command(name="loghistory", description="Admin: retroactively add a match history entry (no Elo change)")
@app_commands.describe(
    winner="Winner",
    loser="Loser",
    score_w="Winner's score",
    score_l="Loser's score",
    winner_elo_after="Winner Elo after the historical match",
    loser_elo_after="Loser Elo after the historical match"
)
async def loghistory(interaction, winner: discord.Member, loser: discord.Member,
                     score_w: int, score_l: int,
                     winner_elo_after: int, loser_elo_after: int):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("No permission", ephemeral=True)

    data = load_data()
    register_user(data, winner.id)
    register_user(data, loser.id)

    append_match_history(
        data,
        winner_id=winner.id,
        loser_id=loser.id,
        score_w=score_w,
        score_l=score_l,
        winner_elo_after=winner_elo_after,
        loser_elo_after=loser_elo_after
    )
    save_data(data)

    await interaction.response.send_message(
        f"Logged historical match: {winner.display_name} {score_w}-{score_l} {loser.display_name} "
        f"(Winner Elo after: {winner_elo_after}, Loser Elo after: {loser_elo_after})"
    )
    

@tree.command(name="setpeak", description="Admin: set a player's peak ELO manually")
@app_commands.describe(
    user="The player whose peak ELO you want to set",
    peak_elo="The peak ELO value to record"
)
async def setpeak(interaction: discord.Interaction,
                  user: discord.Member,
                  peak_elo: int):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("No permission.", ephemeral=True)

    if peak_elo < 0:
        return await interaction.response.send_message("Peak ELO must be non-negative.", ephemeral=True)

    data = load_data()
    register_user(data, user.id)
    entry = data[str(user.id)]

    current_elo = entry.get('elo', 100)

    if 'peak_elo' not in entry:
        entry['peak_elo'] = current_elo

    old_peak = entry['peak_elo']
    entry['peak_elo'] = peak_elo
    save_data(data)

    await interaction.response.send_message(
        f"Peak Updated: {peak_elo}. "
        f"(Current ELO: {current_elo})"
    )

# DOUBLES COMMANDS

@tree.command(name="dstats", description="View a player's doubles stats")
@app_commands.describe(user="The player to look up (optional)")
async def dstats(
    interaction: discord.Interaction,
    user: Optional[discord.Member] = None
):
    user = user or interaction.user
    data = dE.load_data()
    dE.register_user(data, user.id)
    stats = dE.get_stats(data, user.id)

    ranked = sorted(data.items(), key=lambda kv: kv[1]["elo"], reverse=True)
    pos = next((i for i,(uid,_) in enumerate(ranked) if int(uid)==user.id), None)
    rank = pos+1 if pos is not None else "–"

    msg = (
        f"**{user.display_name} | Doubles #{rank}**\n"
        f"> DELO: {stats['elo']}\n"
        f"> Peak DELO: {stats.get('peak_elo', stats['elo'])}\n"
        f"> Wins: {stats['wins']}\n"
        f"> Losses: {stats['losses']}\n"
        f"> Streak: {stats['streak']}\n"
        f"> Total DELO Gained: {stats.get('all_time_gain',0)}\n"
        f"> Total DELO Lost: {stats.get('all_time_loss',0)}\n"
    )

    await interaction.response.send_message(msg)

@tree.command(
    name="dmatch",
    description="Log a doubles match result (admin only)"
)
@app_commands.describe(
    a1="Winner 1",
    a2="Winner 2",
    b1="Loser 1",
    b2="Loser 2",
    score_w="Winner's total score",
    score_l="Loser's total score",
    set_count="(optional) how many sets were played",
    winner_sets="(optional) comma‑sep list of winner's set scores",
    loser_sets="(optional) comma‑sep list of loser's set scores"
)
async def dmatch(
    interaction: discord.Interaction,
    a1: discord.Member,
    a2: discord.Member,
    b1: discord.Member,
    b2: discord.Member,
    score_w: int,
    score_l: int,
    set_count: Optional[int] = None,
    winner_sets: Optional[str] = None,
    loser_sets: Optional[str]   = None
):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("No permission", ephemeral=True)

    player_ids = [a1.id, a2.id, b1.id, b2.id]
    if len(set(player_ids)) != 4:
        return await interaction.response.send_message("All four doubles players must be unique.", ephemeral=True)
    if score_w < 0 or score_l < 0:
        return await interaction.response.send_message("Scores cannot be negative.", ephemeral=True)
    if score_w <= score_l:
        return await interaction.response.send_message("Winner's score must be greater than loser's score.", ephemeral=True)

    data = dE.load_data()
    for p in (a1, a2, b1, b2):
        dE.register_user(data, p.id)

    match_id = new_match_id("D")
    logged_at = utc_now_iso()
    before_snapshot = snapshot_players(data, player_ids)

    before = {
        a1.id: data[str(a1.id)]['elo'],
        a2.id: data[str(a2.id)]['elo'],
        b1.id: data[str(b1.id)]['elo'],
        b2.id: data[str(b2.id)]['elo'],
    }

    result = dE.process_doubles_match(
        data,
        a1.id, a2.id,
        b1.id, b2.id
    )
    after_snapshot = snapshot_players(data, player_ids)
    dE.save_data(data)

    record_match(
        match_id=match_id,
        kind="doubles",
        guild_id=interaction.guild_id,
        channel_id=interaction.channel_id,
        logged_by_id=interaction.user.id,
        players=player_ids,
        before=before_snapshot,
        after=after_snapshot,
        summary={
            "winners": [str(a1.id), str(a2.id)],
            "losers": [str(b1.id), str(b2.id)],
            "score_w": score_w,
            "score_l": score_l,
            "delta_win": result["delta_win"],
            "delta_loss": result["delta_loss"],
            "logged_at": logged_at,
        }
    )

    after = {
        a1.id: data[str(a1.id)]['elo'],
        a2.id: data[str(a2.id)]['elo'],
        b1.id: data[str(b1.id)]['elo'],
        b2.id: data[str(b2.id)]['elo'],
    }

    def paired_stats(p, q):
        wins = data[str(p)]['partners'].get(str(q), 0)
        losses = data[str(p)].get('partners_losses', {}).get(str(q), 0)
        return wins, losses

    w_wins, w_losses = paired_stats(a1.id, a2.id)
    l_wins, l_losses = paired_stats(b1.id, b2.id)

    set_lines = []
    if set_count and winner_sets and loser_sets:
        wlist = winner_sets.split(",")
        llist = loser_sets.split(",")
        if len(wlist) == set_count == len(llist):
            for i in range(set_count):
                set_lines.append(f"> Set {i+1}: {wlist[i].strip()}–{llist[i].strip()}")

    header = (
        f"{a1.mention} ({before[a1.id]}) & {a2.mention} ({before[a2.id]}) "
        f"{score_w}-{score_l} "
        f"{b1.mention} ({before[b1.id]}) & {b2.mention} ({before[b2.id]})"
    )

    middle = (
        f"> Winners +{result['delta_win']}\n"
        f"> Losers  -{result['delta_loss']}"
    )

    footer = (
        f"{a1.display_name} (**{after[a1.id]}**) & {a2.display_name} "
        f"(**{after[a2.id]}**) {w_wins}-{w_losses}\n"
        f"{b1.display_name} (**{after[b1.id]}**) & {b2.display_name} "
        f"(**{after[b2.id]}**) {l_wins}-{l_losses}"
    )

    msg = f"`Match ID: {match_id}`\n" + header + "\n\n"
    if set_lines:
        msg += "\n".join(set_lines) + "\n\n"
    msg += middle + "\n\n" + footer

    await interaction.response.send_message(msg)

@tree.command(name="duos", description="Top 10 Best Doubles")
async def duos(interaction: discord.Interaction):
    data = dE.load_data()

    pair_wins: dict[tuple[int,int], int] = {}
    for pid_str, entry in data.items():
        me = int(pid_str)
        for partner_str, wins in entry.get("partners", {}).items():
            partner = int(partner_str)
            if me < partner:
                pair = (me, partner)
                pair_wins[pair] = wins

    top10 = sorted(pair_wins.items(), key=lambda kv: kv[1], reverse=True)[:10]

    lines = ["**Top Duos**"]
    for i, ((p1, p2), wins) in enumerate(top10, start=1):
        m1 = await interaction.client.fetch_user(p1)
        m2 = await interaction.client.fetch_user(p2)
        lines.append(f"{i}. {m1.display_name} & {m2.display_name} — {wins}W")

    await interaction.response.send_message("\n".join(lines))

@tree.command(name="dleaderboard", description="Top 10 doubles ELO")
async def dleaderboard(interaction: discord.Interaction):
    data = dE.load_data()
    top10 = sorted(data.items(), key=lambda kv: kv[1]["elo"], reverse=True)[:10]
    lines = ["**Doubles ELO Leaderboard**"]
    for i, (uid, stats) in enumerate(top10, start=1):
        user = await interaction.client.fetch_user(int(uid))
        lines.append(f"{i}. {user.display_name} — {stats['elo']}")

    await interaction.response.send_message("\n".join(lines))

@tree.command(
    name="dmodify",
    description="Admin: set a player's doubles elo/wins/losses"
)
@app_commands.describe(
    user="Player whose stat to modify",
    field="Which field to set: elo, wins, losses",
    value="New integer value"
)
async def dmodify(
    interaction: discord.Interaction,
    user: discord.Member,
    field: Literal['elo','wins','losses'],
    value: int
):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("No permission", ephemeral=True)

    data = dE.load_data()
    dE.register_user(data, user.id)
    entry = data[str(user.id)]

    entry[field] = value
    dE.save_data(data)

    await interaction.response.send_message(
        f"{user.display_name}'s **{field}** set to {value}."
    )

def _is_league_admin(interaction: discord.Interaction) -> bool:
    return is_admin(interaction.user) or has_role(interaction.user, ALLOWED_ROLE_IDS)


def _restore_record_for_undo(rec, *, voided_by_id: int, reason: str):
    """
    Restore a logged match and mark it voided.
    Returns (success: bool, message: str).
    """
    log = load_match_log()
    live_rec = find_match(log, rec["match_id"])
    if not live_rec:
        return False, "Could not find that match in match_log.json."
    if live_rec.get("status") != "active":
        return False, f"That match is already {live_rec.get('status', 'not active')}."

    if live_rec.get("kind") == "singles":
        data = load_data()
        save_fn = save_data
    elif live_rec.get("kind") == "doubles":
        data = dE.load_data()
        save_fn = dE.save_data
    else:
        return False, "Unknown match type in match log."

    ok, why = can_restore_match(data, live_rec)
    if not ok:
        return False, why

    restore_before_snapshot(data, live_rec)
    save_fn(data)
    mark_match_voided(log, live_rec, voided_by_id=voided_by_id, reason=reason)
    return True, f"Voided match `{live_rec['match_id']}`. Stats restored to the pre-match snapshot."


async def _match_summary_line(interaction: discord.Interaction, rec) -> str:
    kind = rec.get("kind", "match")
    summary = rec.get("summary", {})
    match_id = rec.get("match_id", "?")

    async def name(uid: str) -> str:
        try:
            user = await interaction.client.fetch_user(int(uid))
            return user.display_name
        except Exception:
            return f"ID:{uid}"

    if kind == "singles":
        winner = await name(summary.get("winner_id"))
        loser = await name(summary.get("loser_id"))
        return f"`{match_id}` singles — {winner} {summary.get('score_w', '?')}-{summary.get('score_l', '?')} {loser}"

    if kind == "doubles":
        winners = summary.get("winners", [])
        losers = summary.get("losers", [])
        w_names = " & ".join([await name(uid) for uid in winners])
        l_names = " & ".join([await name(uid) for uid in losers])
        return f"`{match_id}` doubles — {w_names} {summary.get('score_w', '?')}-{summary.get('score_l', '?')} {l_names}"

    return f"`{match_id}` {kind}"


@tree.command(name="lastmatch", description="Show the most recent active match ID")
async def lastmatch(interaction: discord.Interaction):
    log = load_match_log()
    rec = get_last_active_match(log, guild_id=interaction.guild_id)
    if not rec:
        return await interaction.response.send_message("No active logged matches found.", ephemeral=True)

    line = await _match_summary_line(interaction, rec)
    await interaction.response.send_message(f"Most recent active match:\n{line}")


@tree.command(name="undo", description="Admin: undo the most recent active match")
async def undo(interaction: discord.Interaction):
    if not _is_league_admin(interaction):
        return await interaction.response.send_message("No permission", ephemeral=True)

    log = load_match_log()
    rec = get_last_active_match(log, guild_id=interaction.guild_id)
    if not rec:
        return await interaction.response.send_message("No active logged matches found to undo.", ephemeral=True)

    summary = await _match_summary_line(interaction, rec)
    ok, msg = _restore_record_for_undo(
        rec,
        voided_by_id=interaction.user.id,
        reason="Undo most recent match"
    )
    if not ok:
        return await interaction.response.send_message(msg, ephemeral=True)

    await interaction.response.send_message(f"✅ {msg}\n{summary}")


@tree.command(name="voidmatch", description="Admin: void a match by match ID and restore stats")
@app_commands.describe(
    match_id="The match ID, for example S-A1B2C3D4",
    reason="Optional reason for voiding the match"
)
async def voidmatch(interaction: discord.Interaction, match_id: str, reason: Optional[str] = None):
    if not _is_league_admin(interaction):
        return await interaction.response.send_message("No permission", ephemeral=True)

    log = load_match_log()
    rec = find_match(log, match_id)
    if not rec:
        return await interaction.response.send_message("No match found with that ID.", ephemeral=True)
    if rec.get("guild_id") != str(interaction.guild_id):
        return await interaction.response.send_message("That match ID belongs to another server or has no server attached.", ephemeral=True)

    summary = await _match_summary_line(interaction, rec)
    ok, msg = _restore_record_for_undo(
        rec,
        voided_by_id=interaction.user.id,
        reason=reason or "Voided by admin"
    )
    if not ok:
        return await interaction.response.send_message(msg, ephemeral=True)

    await interaction.response.send_message(f"✅ {msg}\n{summary}")


@tree.command(name="editmatch", description="Admin: edit a singles match by voiding it and logging the corrected result")
@app_commands.describe(
    match_id="The original singles match ID",
    winner="Correct winner",
    loser="Correct loser",
    score_w="Correct winner score",
    score_l="Correct loser score"
)
async def editmatch(
    interaction: discord.Interaction,
    match_id: str,
    winner: discord.Member,
    loser: discord.Member,
    score_w: int,
    score_l: int
):
    if not _is_league_admin(interaction):
        return await interaction.response.send_message("No permission", ephemeral=True)
    if winner.id == loser.id:
        return await interaction.response.send_message("Winner and loser must be different players.", ephemeral=True)
    if score_w < 0 or score_l < 0:
        return await interaction.response.send_message("Scores cannot be negative.", ephemeral=True)
    if score_w <= score_l:
        return await interaction.response.send_message("Winner's score must be greater than loser's score.", ephemeral=True)

    log = load_match_log()
    old_rec = find_match(log, match_id)
    if not old_rec:
        return await interaction.response.send_message("No match found with that ID.", ephemeral=True)
    if old_rec.get("guild_id") != str(interaction.guild_id):
        return await interaction.response.send_message("That match ID belongs to another server or has no server attached.", ephemeral=True)
    if old_rec.get("kind") != "singles":
        return await interaction.response.send_message("Use `/deditmatch` for doubles matches.", ephemeral=True)
    if old_rec.get("status") != "active":
        return await interaction.response.send_message("That match is not active, so it cannot be edited.", ephemeral=True)

    data = load_data()
    ok, why = can_restore_match(data, old_rec)
    if not ok:
        return await interaction.response.send_message(why, ephemeral=True)

    # Step 1: restore the old match's before-state.
    restore_before_snapshot(data, old_rec)
    mark_match_voided(log, old_rec, voided_by_id=interaction.user.id, reason="Edited by admin")

    # Step 2: log the corrected match from that restored state.
    register_user(data, winner.id)
    register_user(data, loser.id)
    new_id = new_match_id("S")
    logged_at = utc_now_iso()
    before = snapshot_players(data, (winner.id, loser.id))
    result = process_match(
        data,
        winner.id,
        loser.id,
        score_w=score_w,
        score_l=score_l,
        match_id=new_id,
        logged_at=logged_at
    )
    after = snapshot_players(data, (winner.id, loser.id))
    save_data(data)

    record_match(
        match_id=new_id,
        kind="singles",
        guild_id=interaction.guild_id,
        channel_id=interaction.channel_id,
        logged_by_id=interaction.user.id,
        players=(winner.id, loser.id),
        before=before,
        after=after,
        summary={
            "winner_id": str(winner.id),
            "loser_id": str(loser.id),
            "score_w": score_w,
            "score_l": score_l,
            "winner_elo_before": result["winner_elo_before"],
            "loser_elo_before": result["loser_elo_before"],
            "winner_elo_after": result["winner_elo_after"],
            "loser_elo_after": result["loser_elo_after"],
            "elo_gain": result["elo_gain"],
            "elo_loss": result["elo_loss"],
            "bonus": result["bonus"],
            "logged_at": logged_at,
        },
        edited_from=old_rec["match_id"]
    )

    await interaction.response.send_message(
        f"✅ Edited match `{old_rec['match_id']}`.\n"
        f"Old match was voided. New match ID: `{new_id}`\n"
        f"{winner.mention} {score_w}-{score_l} {loser.mention}"
    )


@tree.command(name="deditmatch", description="Admin: edit a doubles match by voiding it and logging the corrected result")
@app_commands.describe(
    match_id="The original doubles match ID",
    a1="Correct winning team player 1",
    a2="Correct winning team player 2",
    b1="Correct losing team player 1",
    b2="Correct losing team player 2",
    score_w="Correct winning team score",
    score_l="Correct losing team score"
)
async def deditmatch(
    interaction: discord.Interaction,
    match_id: str,
    a1: discord.Member,
    a2: discord.Member,
    b1: discord.Member,
    b2: discord.Member,
    score_w: int,
    score_l: int
):
    if not _is_league_admin(interaction):
        return await interaction.response.send_message("No permission", ephemeral=True)

    player_ids = [a1.id, a2.id, b1.id, b2.id]
    if len(set(player_ids)) != 4:
        return await interaction.response.send_message("All four doubles players must be unique.", ephemeral=True)
    if score_w < 0 or score_l < 0:
        return await interaction.response.send_message("Scores cannot be negative.", ephemeral=True)
    if score_w <= score_l:
        return await interaction.response.send_message("Winner's score must be greater than loser's score.", ephemeral=True)

    log = load_match_log()
    old_rec = find_match(log, match_id)
    if not old_rec:
        return await interaction.response.send_message("No match found with that ID.", ephemeral=True)
    if old_rec.get("guild_id") != str(interaction.guild_id):
        return await interaction.response.send_message("That match ID belongs to another server or has no server attached.", ephemeral=True)
    if old_rec.get("kind") != "doubles":
        return await interaction.response.send_message("Use `/editmatch` for singles matches.", ephemeral=True)
    if old_rec.get("status") != "active":
        return await interaction.response.send_message("That match is not active, so it cannot be edited.", ephemeral=True)

    data = dE.load_data()
    ok, why = can_restore_match(data, old_rec)
    if not ok:
        return await interaction.response.send_message(why, ephemeral=True)

    restore_before_snapshot(data, old_rec)
    mark_match_voided(log, old_rec, voided_by_id=interaction.user.id, reason="Edited by admin")

    for p in (a1, a2, b1, b2):
        dE.register_user(data, p.id)

    new_id = new_match_id("D")
    logged_at = utc_now_iso()
    before = snapshot_players(data, player_ids)
    result = dE.process_doubles_match(data, a1.id, a2.id, b1.id, b2.id)
    after = snapshot_players(data, player_ids)
    dE.save_data(data)

    record_match(
        match_id=new_id,
        kind="doubles",
        guild_id=interaction.guild_id,
        channel_id=interaction.channel_id,
        logged_by_id=interaction.user.id,
        players=player_ids,
        before=before,
        after=after,
        summary={
            "winners": [str(a1.id), str(a2.id)],
            "losers": [str(b1.id), str(b2.id)],
            "score_w": score_w,
            "score_l": score_l,
            "delta_win": result["delta_win"],
            "delta_loss": result["delta_loss"],
            "logged_at": logged_at,
        },
        edited_from=old_rec["match_id"]
    )

    await interaction.response.send_message(
        f"✅ Edited doubles match `{old_rec['match_id']}`.\n"
        f"Old match was voided. New match ID: `{new_id}`\n"
        f"{a1.mention} & {a2.mention} {score_w}-{score_l} {b1.mention} & {b2.mention}"
    )


# LAUNCH COMMANDS

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    await client.wait_until_ready()
    try:
        synced_guild=await tree.sync(guild=discord.Object(id=DEV_GUILD_ID))
        print(f"Synced {len(synced_guild)} commands to guild {DEV_GUILD_ID}")
    except Exception as e:
        print(f"Guild sync failed: {e}")
    asyncio.create_task(_global_sync())

async def _global_sync():
    await client.wait_until_ready()
    try:
        synced_global=await tree.sync()
        print(f"Globally synced {len(synced_global)} commands")
    except Exception as e:
        print(f"Global sync failed: {e}")
        
client.run(botToken)
