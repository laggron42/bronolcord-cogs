import discord

from redbot.core import commands
from redbot.core import checks
from redbot.core.bot import Red
from redbot.core.utils import menus
from redbot.core.utils.chat_formatting import pagify

EMOJIS = {
    "1️⃣": 1,
    "2️⃣": 2,
    "3️⃣": 3,
    "4️⃣": 4,
    "5️⃣": 5,
    "6️⃣": 6,
    "7️⃣": 7,
    "8️⃣": 8,
    "9️⃣": 9,
}


class SetParser:
    # https://github.com/Cog-Creators/Red-DiscordBot/blob/6162b0f2bdd9491ea2b51d79f7a86909ee3cab96/redbot/cogs/economy/economy.py#L100
    def __init__(self, argument):
        allowed = ("+", "-")
        self.sum = int(argument)
        if argument and argument[0] in allowed:
            if self.sum < 0:
                self.operation = "withdraw"
            elif self.sum > 0:
                self.operation = "deposit"
            else:
                raise RuntimeError
        elif argument.isdigit():
            self.operation = "set"
        else:
            raise RuntimeError


async def is_mod_or_anim(ctx: commands.Context):
    return await ctx.bot.is_mod(ctx.author) or ctx.cog.ANIMATEUR_ROLE_ID in [
        x.id for x in ctx.author.roles
    ]


class BlindTest(commands.Cog):
    """
    Outils pour les blind tests.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.score = {}
        self.BT_CHANNEL_ID = 738440691822100491
        self.ADMIN_CHANNEL_ID = 682562369305706605
        self.ANIMATEUR_ROLE_ID = 667004287750111233

    @commands.command(name="score")
    @commands.check(is_mod_or_anim)
    async def _score(self, ctx: commands.Context, member: discord.Member, score: SetParser = None):
        """
        Édite ou affiche le score d'un membre.

        Exemples :
        - `[p]score @Laggron` --> Affiche
        - `[p]score @Laggron +2` --> Ajoute 2
        - `[p]score @Laggron -1` --> Retire 1
        - `[p]score @Laggron 4` --> Règle sur 4
        """
        if score is None:
            await ctx.send(f"Score de {str(member)}: {self.score.get(member.id, 0)}")
            return
        old_score = self.score.get(member.id, 0)
        if (score.operation == "set" and score.sum < 0) or (old_score + score.sum < 0):
            await ctx.send("Vous ne pouvez pas régler de score négatif.")
            return
        if score.operation == "set" or old_score == 0:
            self.score[member.id] = score.sum
        else:
            self.score[member.id] += score.sum
        await ctx.tick()

    @commands.command()
    @commands.check(is_mod_or_anim)
    async def classement(self, ctx: commands.Context):
        """
        Affiche le classement.
        """
        scores = dict(sorted(self.score.items(), key=lambda m: m[1], reverse=True))
        message = "__Classement :__\n\n"
        for member_id, score in scores.items():
            member = ctx.guild.get_member(member_id)
            message += f"{score}: {member or member_id}\n"
        if len(message) > 2000:
            pages = []
            for page in pagify(message):
                pages.append(page)
            await menus.menu(ctx, pages, menus.DEFAULT_CONTROLS)
        else:
            await ctx.send(message)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if reaction.message.guild is None:
            return
        if reaction.message.channel.id != self.BT_CHANNEL_ID:
            return
        try:
            num = EMOJIS[reaction.emoji]
        except KeyError:
            return
        if self.ANIMATEUR_ROLE_ID not in [x.id for x in user.roles] and not await self.bot.is_mod(
            user
        ):
            return
        try:
            self.score[reaction.message.author.id] += num
        except KeyError:
            self.score[reaction.message.author.id] = num
        channel = reaction.message.guild.get_channel(self.ADMIN_CHANNEL_ID)
        s = "s" if num > 1 else ""
        await channel.send(
            f":information_source: **{num}** point{s} ajouté{s} "
            f"à {reaction.message.author} par {user}."
        )
