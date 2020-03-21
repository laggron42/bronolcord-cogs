import discord
import asyncio
import re

from datetime import datetime, timedelta

from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.bot import Config
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import ReactionPredicate
from redbot.core.utils.chat_formatting import text_to_file, pagify, humanize_list

MESSAGE_CHECK = re.compile(r"^je participe\.?$", flags=re.I)
CHECKIN_MESSAGE_CHECK = re.compile(r"^!?check\.?$", flags=re.I)


class ProgressionMenu:
    """
    Tools for all progress messages.
    """

    def __init__(
        self,
        bot: Red,
        ctx: commands.Context,
        embed: discord.Embed,
        limit: int,
        text: str = None,
        interval: float = 0.5,
        wait_before_start: int = 0,
        time: datetime = None,
    ):
        self.bot = bot
        self.ctx = ctx
        self.embed = embed
        self.limit = limit
        self.text = text
        self.interval = interval
        self.wait_before_start = wait_before_start
        self.time = time
        self.current = 0
        self.finished = True
        self.message: discord.Message
        self.update_message_task: asyncio.Task
        self.cancel_task: asyncio.Task

    async def edit_message_loop(self):
        while True:
            await self.edit_message()
            await asyncio.sleep(self.interval)

    async def edit_message(self):
        # https://github.com/Cog-Creators/Red-DiscordBot/blob/V3/develop/redbot/cogs/audio/audio.py#L3920
        print("begin edit")
        sections = 40
        progress = round((self.current / self.limit) * sections)
        bar = "="
        seek = ">"
        empty = " "
        text = ""
        for i in range(sections):
            if i < progress:
                text += bar
            elif i == progress:
                text += seek
            else:
                text += empty
        percent = round(self.current / self.limit * 100, 2)
        self.embed.set_field_at(
            0,
            name="Progression",
            value=f"`[{text}]`\n{self.current}/{self.limit} ({percent}%) {self.text}",
            inline=False,
        )
        if self.time:
            self.embed.set_field_at(
                1, name="Progression", value=str(self.time - datetime.now()), inline=False,
            )

        await self.message.edit(embed=self.embed)

    async def task(self):
        raise NotImplementedError

    async def before_run(self):
        pass

    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if self.finished is True:
            return
        if reaction.message.id != self.message.id:
            return
        if user.id != self.ctx.author.id:
            return
        try:
            await self.message.remove_reaction("❌", user)
        except discord.errors.HTTPException:
            pass
        message = await self.ctx.send("Annuler ?")
        pred = ReactionPredicate.yes_or_no(message, self.ctx.author)
        start_adding_reactions(message, ReactionPredicate.YES_OR_NO_EMOJIS)
        try:
            await self.bot.wait_for("reaction_add", check=pred, timeout=20)
        except asyncio.TimeoutError:
            await message.delete()
            return
        if pred.result is False:
            await message.delete()
            return
        self.cancel_task = self.bot.loop.create_task(self.cancel())

    async def _cancel(self):
        self.finished = True
        self.bot.remove_listener(self.on_reaction_add)
        self.update_message_task.cancel()
        # update one last time for a clean 100%
        await self.edit_message()

    async def cancel(self):
        # overwrite this class and do stuff, but always call self._cancel
        await self._cancel()

    async def initialize(self):
        self.message = await self.ctx.send(embed=self.embed)
        await self.message.add_reaction("❌")
        self.bot.add_listener(self.on_reaction_add)
        await self.before_run()

    async def _run(self):
        await self.initialize()
        await asyncio.sleep(self.wait_before_start)
        self.finished = False
        self.update_message_task = self.bot.loop.create_task(self.edit_message_loop())

    async def run(self):
        await self._run()
        await self.task()


class UpdateRoles(ProgressionMenu):
    """
    Ajoute des rôles.
    """

    def __init__(
        self,
        bot: Red,
        ctx: commands.Context,
        members: list,
        roles: list,
        reason: str,
        add_roles: bool = True,
    ):
        embed = discord.Embed(title="Ajout des rôles")
        if len(members) < 11:
            eta = 1
        else:
            eta = (len(members) // 10) * 10
        if len(roles) > 1:
            text = "des rôles " + humanize_list([x.name for x in roles])
        else:
            text = "du rôle " + roles[0].name
        embed.description = (
            f"{'Ajout' if add_roles else 'Retrait'} {text} à {len(members)} membres...\n"
            f"Temps estimé : {eta} secondes"
        )
        embed.add_field(name="Progression", value="Démarrage...", inline=False)
        super().__init__(bot=bot, ctx=ctx, embed=embed, limit=len(members), text="rôles ajoutés")
        self.members = members
        self.roles = roles
        self.reason = reason
        self.add_roles = add_roles
        self.fails = []

    async def task(self):
        if self.add_roles is True:
            func = lambda x: x.add_roles
        else:
            func = lambda x: x.remove_roles
        for member in self.members:
            try:
                await func(member)(*self.roles, reason=self.reason)
            except discord.errors.HTTPException as e:
                self.fails.append((member, e))
            else:
                self.current += 1
        self.cancel_task = self.bot.loop.create_task(self.cancel())

    async def cancel(self):
        await self._cancel()
        file = None
        if self.fails:
            text = ""
            for member, e in self.fails:
                text += f"{str(member)} ({member.id}): {type(e)} {e.args[0]}\n"
            file = text_to_file(text, filename="erreurs.txt")
        if len(self.roles) > 1:
            text = "Rôles " + humanize_list([x.name for x in self.roles])
        else:
            text = "Rôle " + self.roles[0].name
        await self.ctx.send(
            f"{text} {'ajouté' if self.add_roles else 'retiré'} "
            f"à {self.current}/{self.limit} membres.",
            file=file,
        )


class Inscription(ProgressionMenu):
    """
    Inscriptions du vendredi et samedi après-midi.
    """

    def __init__(
        self,
        bot: Red,
        data: Config,
        ctx: commands.Context,
        limit: int,
        channel: discord.TextChannel,
        role: discord.Role,
        participant_role: discord.Role,
        blacklist: list,
    ):
        embed = discord.Embed(title="Inscription au tournoi")
        embed.description = f"L'inscription est en cours dans le channel {channel.mention}"
        embed.add_field(name="Progression", value="Démarrage dans 10 secondes...", inline=True)
        embed.set_footer(text="Cliquez sur ❌ pour annuler l'inscription.")
        embed.colour = 0x00FF33
        super().__init__(bot, ctx, embed, limit, text="membres inscrits", wait_before_start=10)
        self.data = data
        self.channel = channel
        self.role = role
        self.participant_role = participant_role
        self.blacklist = blacklist

    async def on_message(self, message: discord.Message):
        if self.finished is True:
            return
        if message.channel.id != self.channel.id:
            return
        if not MESSAGE_CHECK.match(message.content):
            return
        member = message.author
        if member.id in self.blacklist:
            return
        if self.participant_role in member.roles:
            return
        async with self.data.guild(self.ctx.guild).current() as participants:
            if member.id in participants:
                return
            participants.append(member.id)
        self.current += 1
        if self.current >= self.limit:
            self.finished = True
            self.cancel_task = self.bot.loop.create_task(self.cancel())
        try:
            await message.add_reaction("✅")
        except Exception:
            pass

    async def task(self):
        self.bot.add_listener(self.on_message)
        await self.channel.set_permissions(
            self.role, send_messages=True, read_messages=True, reason="Ouverture des inscriptions"
        )

    async def before_run(self):
        await self.channel.send(
            "__Inscription pour le prochain tournoi__\n\n"
            "- Envoyez `Je participe` dans ce channel pour s'inscrire\n"
            "- Éditer le message ne marche pas\n"
            "- Si vous pensez qu'il y a eu un problème, contactez un PK Thunder\n\n"
            "Ouverture dans 10 secondes."
        )

    async def cancel(self):
        await self._cancel()
        self.bot.remove_listener(self.on_message)
        await self.channel.set_permissions(
            self.role, send_messages=False, read_messages=True, reason="Fermeture des inscriptions"
        )
        await self.channel.send("Fin des inscriptions.")
        await self.ctx.send(
            f"Inscription terminée, {self.current} membres enregistrés. Envoi du fichier..."
        )
        async with self.ctx.typing():
            participants = await self.data.guild(self.ctx.guild).current()
            content = "\n".join((str(self.ctx.guild.get_member(x)) for x in participants))
            file = text_to_file(content, "participants.txt")
            await self.ctx.send(file=file)


class CheckIn(ProgressionMenu):
    """
    Phase de check-in
    """

    def __init__(
        self,
        bot: Red,
        data: Config,
        ctx: commands.Context,
        channel: discord.TextChannel,
        checkin_role: discord.Role,
        participant_role: discord.Role,
    ):
        embed = discord.Embed(title="Check-in")
        embed.description = f"Le check-in est en cours dans le channel {channel.mention}"
        embed.add_field(name="Progression", value="Démarrage dans 10 secondes.", inline=False)
        embed.add_field(name="Temps restant", value="0:30:00", inline=False)
        embed.set_footer(text="Cliquez sur ❌ pour annuler l'inscription.")
        embed.colour = 0x0033FF
        super().__init__(
            bot,
            ctx,
            embed,
            len(participant_role.members),
            "joueurs check",
            wait_before_start=10,
            time=datetime.now() + timedelta(seconds=1800),
        )
        self.data = data
        self.channel = channel
        self.checkin_role = checkin_role
        self.participant_role = participant_role
        self.checked = []
        self.failed = []
        self.to_blacklist: list

    async def on_message(self, message: discord.Message):
        if self.finished is True:
            return
        if message.channel.id != self.channel.id:
            return
        if not CHECKIN_MESSAGE_CHECK.match(message.content):
            return
        member = message.author
        if self.participant_role not in member.roles:
            return
        if member in self.checked:
            return
        try:
            await member.add_roles(self.checkin_role, reason="Check-in tournoi")
        except discord.errors.HTTPException as e:
            self.failed.append((member, e))
            return
        self.checked.append(member)
        self.current = len(self.checked)
        if self.current >= self.limit:
            self.finished = True
            self.cancel_task = self.bot.loop.create_task(self.cancel())
        try:
            await message.add_reaction("✅")
        except Exception:
            pass

    async def task(self):
        self.bot.add_listener(self.on_message)
        await self.channel.set_permissions(
            self.participant_role,
            send_messages=True,
            read_messages=True,
            reason="Ouverture du check-in",
        )

    async def before_run(self):
        await self.channel.send(
            "__Check pour le prochain tournoi__\n\n"
            "- Envoyez `check` dans ce channel pour confirmer l'inscription\n"
            "- Éditer le message ne marche pas\n"
            "- Si vous pensez qu'il y a eu un problème, contactez un PK Thunder\n\n"
            "Ouverture dans 10 secondes."
        )

    async def cancel(self):
        await self._cancel()
        self.bot.remove_listener(self.on_message)
        await self.channel.set_permissions(
            self.participant_role,
            read_messages=True,
            send_messages=False,
            reason="Fermeture du check-in",
        )
        await self.channel.send("Fin du check-in.")
        await self.ctx.send(
            f"Check-in terminé, {len(self.checked)} membres enregistrés. Envoi du fichier..."
        )
        async with self.ctx.typing():
            content = "\n".join((str(x) for x in self.checked))
            files = [text_to_file(content, "participants.txt")]
            if self.failed:
                content = "\n".join(f"{str(x)}: {type(e)}: {e.args[0]}" for x, e in self.failed)
                files.append(text_to_file(content, "fails.txt"))
            await self.ctx.send(files=files)
        self.to_blacklist = [x.id for x in self.participant_role.members if x not in self.checked]
        await self.data.guild(self.ctx.guild).blacklisted.set(self.to_blacklist)
        await self.ctx.send(
            f"La blacklist a été réintialisée, puis les {len(self.to_blacklist)} n'ayant pas "
            "check y ont été ajoutés. Ils ne pourront pas participer au prochain tournoi.\n"
            f"Tapez `{self.ctx.clean_prefix}tournamentban list` pour voir la blacklist."
        )
