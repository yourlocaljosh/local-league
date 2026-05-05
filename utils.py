def is_admin(member):
    return member.guild_permissions.administrator


def format_stats(user, stats):
    return (
        f"**{user.display_name}**\n"
        f"> ELO: {stats['elo']}\n"
        f"> Wins: {stats['wins']}\n"
        f"> Losses: {stats['losses']}\n"
        f"> Current Streak: {stats['streak']}"
    )


def has_role(member, role_ids):
    return any(role.id in role_ids for role in member.roles)
