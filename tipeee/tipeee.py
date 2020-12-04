from datetime import datetime
import discord
import sys
import traceback
import aiohttp

from fuzzywuzzy import fuzz, process
from typing import List, Optional, TYPE_CHECKING

from redbot.core import commands
from redbot.core import checks
from redbot.core import Config
from discord.ext import tasks

if TYPE_CHECKING:
    from redbot.core.bot import Red

BASE_URL = "https://api.tipeee.com/"


class Tipeee(commands.Cog):
    """
    Annonce les nouveaux tippers.
    """

    default_guild = {
        "user": None,
        "channel": None,
        "roles": [],
        "tippers": [],
    }

    def __init__(self, bot: "Red"):
        self.bot = bot
        self.data = Config.get_conf(self, 260)
        self.data.register_guild(**self.default_guild)
        self.task_errors = 0
        self.loop_task.start()

    @commands.group()
    @checks.admin_or_permissions(administrator=True)
    async def tipeeeset(self, ctx: commands.Context):
        """
        Configuration de Tipeee
        """
        pass

    @tipeeeset.command(name="user")
    async def tipeeeset_user(self, ctx: commands.Context, user: Optional[str]):
        """
        Règle l'utilisateur Tipeee à observer.
        """
        await self.data.guild(ctx.guild).user.set(user)
        if user is None:
            await ctx.send("Utilisateur réinitialisé.")
        else:
            await ctx.send("Utilisateur configuré.")

    @tipeeeset.command(name="channel")
    async def tipeeeset_channel(self, ctx: commands.Context, *, channel: discord.TextChannel):
        """
        Règle le channel où annoncer les nouveaux tippers.
        """
        await self.data.guild(ctx.guild).channel.set(channel.id)
        await ctx.send("Channel configuré.")

    @tipeeeset.command(name="role")
    async def tipeeeset_role(self, ctx: commands.Context, *, role: discord.Role):
        """
        Ajoute ou retire un rôle considéré comme tipper.

        Ces rôles ne seront pas ajoutés ou retirés, mais utilisés pour savoir si il faut suggérer \
d'ajouter ou de retirer un rôle à un membre.
        """
        async with self.data.guild(ctx.guild).roles() as roles:
            try:
                roles.remove(role.id)
            except ValueError:
                roles.append(role.id)
                await ctx.send("Role ajouté.")
            else:
                await ctx.send("Role retiré.")

    @tipeeeset.command(name="settings")
    async def tipeeeset_settings(self, ctx: commands.Context):
        """
        Affiche les réglages.
        """
        data = await self.data.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(data["channel"])
        await ctx.send(
            (
                "Utilisateur configuré : {user}\n"
                "Channel d'annonce : {channel}\n"
                "Rôles de tippers : {roles}\n"
                "Nombre de tippers actuels enregistrés : {tippers}"
            ).format(
                user=data["user"],
                channel=channel.mention if channel else channel,
                roles=", ".join(
                    [x.name for x in filter(None, [ctx.guild.get_role(y) for y in data["roles"]])]
                ),
                tippers=len(data["tippers"]),
            )
        )

    @tasks.loop(minutes=15)
    async def loop_task(self):
        all_data = await self.data.all_guilds()
        for guild_id, data in all_data.items():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            channel = guild.get_channel(await self.data.guild(guild).channel())
            if not channel:
                continue
            await self._look_for_tippers(guild, channel, data)

    async def _look_for_tippers(
        self, guild: discord.Guild, channel: discord.TextChannel, data: dict
    ):
        user = data.get("user")
        if user is None:
            return
        saved_tippers = data.get("tippers", [])
        base_saved_tippers = [x[0] for x in saved_tippers]
        tippers = await self._fetch_tippers(user)
        base_tippers = [x[0] for x in tippers]
        new_tippers = [x for x in tippers if x[0] not in base_saved_tippers]
        lost_tippers = [x for x in saved_tippers if x[0] not in base_tippers]
        if not new_tippers and not lost_tippers:
            return
        for tipper in new_tippers:
            await self._announce_new(user, guild, channel, tipper)
        for tipper in lost_tippers:
            await self._announce_lost(user, guild, channel, data["roles"], tipper)
        await self.data.guild(guild).tippers.set(tippers)

    async def _request(self, url, params={}):
        async with aiohttp.ClientSession() as session:
            async with session.get(BASE_URL + url, params=params) as result:
                result.raise_for_status()
                return await result.json()

    async def _get_avatar(self, user):
        result = await self._request(f"v2.0/users/{user}")
        if "avatar" not in result:
            return None
        return BASE_URL + result["avatar"]["path"] + "/" + result["avatar"]["filename"]

    async def _fetch_tippers(self, user: str) -> List[str]:
        actual_result = []
        url = f"v2.0/projects/{user}/top/tippers"
        arguments = {"page": 1, "perPage": 250}
        result = await self._request(url, arguments)
        for item in result["items"]:
            actual_result.append((item["username_canonical"], item["pseudo"]))
        return actual_result

    async def _announce_new(
        self, user: str, guild: discord.Guild, channel: discord.TextChannel, tipper
    ):
        names = {x: x.display_name for x in guild.members}
        extracted = process.extract(tipper[1], names, limit=5, scorer=fuzz.QRatio)
        extracted = list(filter(lambda x: x[1] > 40, extracted))
        embed = discord.Embed(
            title=f"Nouveau tipper : {tipper[1]}",
            url=f"https://tipeee.com/{user}",
            color=discord.Colour.green(),
        )
        embed.set_thumbnail(url=(await self._get_avatar(tipper[0])) or discord.Embed.Empty)
        if extracted:
            description = "\n".join([f"{x[2].mention} *{x[1]}%*" for x in extracted])
        else:
            description = "Aucun membre potentiel trouvé."
        embed.add_field(name="Membres potentiels", value=description)
        await channel.send(embed=embed, content=" ".join([x[2].mention for x in extracted]))

    async def _announce_lost(
        self, user: str, guild: discord.Guild, channel: discord.TextChannel, roles: list, tipper
    ):
        roles = list(filter(None, [guild.get_role(x) for x in roles]))
        members = [x for x in guild.members if any(y in x.roles for y in roles)]
        names = {x: x.display_name for x in members}
        extracted = process.extract(tipper[1], names, limit=2, scorer=fuzz.QRatio)
        extracted = list(filter(lambda x: x[1] > 40, extracted))
        embed = discord.Embed(
            title=f"Tipper perdu : {tipper[1]}",
            url=f"https://tipeee.com/{user}",
            color=discord.Colour.red(),
        )
        embed.set_thumbnail(url=(await self._get_avatar(tipper[0])) or discord.Embed.Empty)
        if extracted:
            p = "s" if len(extracted) > 1 else ""
            embed.description = f"Membre{p} potentiel{p} :\n" + "\n".join(
                [f"{x[2].mention} *{x[1]}%*" for x in extracted]
            )
        else:
            embed.description = "Aucun membre potentiel avec rôle trouvé."
        await channel.send(embed=embed, content=" ".join([x[2].mention for x in extracted]))

    @loop_task.error
    async def on_task_error(self, *args):
        exception = args[-1]
        print("Unhandled exception in Tipeee internal background task.", file=sys.stderr)
        traceback.print_exception(
            type(exception), exception, exception.__traceback__, file=sys.stderr
        )
        self.task_errors += 1
        if self.task_errors < 5:
            self.loop_task.start()
