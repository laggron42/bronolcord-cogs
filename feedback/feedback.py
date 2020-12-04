import discord
import random

from typing import TYPE_CHECKING

from redbot.core import commands
from redbot.core import checks
from redbot.core import Config
from redbot.core.utils.tunnel import Tunnel

if TYPE_CHECKING:
    from redbot.core.bot import Red

GUILD_ID = 598541499608858634  # Super Smash Bronol
ROLE_ID = 762819547564474388  # PK Staff


def check(ctx: commands.Context):
    bronolcord = ctx.bot.get_guild(GUILD_ID)
    role = bronolcord.get_role(ROLE_ID)
    member = bronolcord.get_member(ctx.author)
    return role in member.roles


class Feedback(commands.Cog):
    """
    Feedback anonyme pour le Bronolcord.
    """

    def __init__(self, bot: "Red"):
        self.bot = bot
        self.data = Config.get_conf(self, 260)
        self.data.register_global(feedbackchannel=None)

    @commands.command()
    @checks.is_owner()
    async def feedbackset(self, ctx: commands.Context, *, channel: discord.TextChannel):
        """
        Règle le channel de feedback.
        """
        await self.data.feedbackchannel.set(channel.id)
        await ctx.tick()

    @commands.command()
    async def feedback(self, ctx: commands.Context, *, content: str):
        """
        Envoyez un feedback anonyme pour le staff du serveur de Bronol.

        Ni votre pseudo, ni votre ID sont affichés. Cependant, un identifiant unique est généré à \
partir du votre, il est donc possible de savoir qu'une
        """
        channel_id = await self.data.feedbackchannel()
        if not channel_id:
            await ctx.send("Le feedback n'est pas configuré par les admins.")
            return
        channel = self.bot.get_channel(channel_id)
        if not channel:
            await ctx.send("Le channel de feedback a été perdu. Contactez les admins.")
            return
        files = await Tunnel.files_from_attatch(ctx.message)
        if ctx.guild:
            await ctx.message.delete()
        embed = discord.Embed(title="Feedback anonyme", color=random.randint(0, 16777215))
        embed.description = content
        embed.timestamp = ctx.message.created_at
        await channel.send(embed=embed, files=files)
        if not ctx.guild:
            await ctx.tick()

    async def cog_command_error(self, ctx: commands.Context, error):
        if (
            ctx.guild
            and ctx.guild.id == GUILD_ID
            and isinstance(error, commands.CommandOnCooldown)
        ):
            await ctx.message.delete()
        await self.bot.on_command_error(ctx, error, unhandled_by_cog=True)
