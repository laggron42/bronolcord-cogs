import discord
import challonge
import logging
import os
import re
import asyncio

from typing import TYPE_CHECKING, Union
from datetime import datetime

from redbot.core import commands
from redbot.core import checks
from redbot.core import Config
from redbot.core.utils import menus
from redbot.core.utils.chat_formatting import pagify, text_to_file
from redbot.core.data_manager import cog_data_path

if TYPE_CHECKING:
    from redbot.core.bot import Red

log = logging.getLogger("laggron.tournamentmanager")

CHALLONGE_TOURNAMENT_URL_REGEX = re.compile(r"(https?://challonge\.com/)(fr/)?(?P<url>\S+)/?")
CONFIRM_MESSAGE_REGEX = re.compile(r"je participe")

TOURNAMENT = "TOURNAMENT"  # custom config group identifier, prevents typos
TOURNAMENT_NAME = "SUPER SMASH BRONOL - {MAJOR}#{index}"
TOURNAMENT_URL = "ssbronol{index}"
TOURNAMENT_DESCRIPTION = """Tournois hebdomadaire du serveur Discord de Bronol !
Rejoindre le serveur : https://discord.gg/tprzMdX
Chaine YouTube de Bronol : https://www.youtube.com/user/Bronol"""
TOURNAMENT_PARAMETERS = {
    "open_signup": False,
    "notify_users_when_matches_open": True,
    "notify_users_when_the_tournament_ends": True,
    "description": TOURNAMENT_DESCRIPTION,
}


class TournamentConverter(commands.Converter):
    def __init__(self):
        self.data: Config
        self.challonge: challonge.User
        self.guild: discord.Guild

    async def find(self, *, identifier: str = None, url: str = None):
        if identifier is None and url is None:
            raise KeyError("Provide identifier or URL")
        if identifier:
            if identifier not in (await self.data.custom(TOURNAMENT, self.guild.id).all()).keys():
                raise commands.BadArgument("Ce tournoi n'est pas enregistré")
            try:
                tournament = await self.challonge.get_tournament(t_id=identifier)
            except challonge.APIException as e:
                log.error(f"Tournoi {identifier} non trouvé.", exc_info=e)
                raise commands.BadArgument(
                    "Une erreur s'est produite lors de la recherche du tournoi. "
                    "Regarder les logs pour plus d'informations."
                ) from e
            except asyncio.TimeoutError as e:
                log.error(f"Timeout with get_tournament: {identifier}", exc_info=e)
                raise commands.BadArgument("La requête a expirée.") from e
            return tournament
        if url:
            tournaments = await self.data.custom(TOURNAMENT, self.guild.id).all()
            for tournament in tournaments:
                if url == tournament["url"]:
                    try:
                        tournament = await self.challonge.get_tournament(url=url)
                    except challonge.APIException as e:
                        log.error(f"Tournoi {identifier} non trouvé.", exc_info=e)
                        raise commands.BadArgument(
                            f"Une erreur s'est produite lors de la recherche du tournoi. "
                            "Regarder les logs pour plus d'informations."
                        ) from e
                    return tournament

    async def convert(self, ctx, text):
        cog = ctx.bot.get_cog("TournamentManager")
        try:
            await cog._set_challonge()
        except NotImplementedError:
            await cog._help_api_keys(ctx)
        self.guild = ctx.guild
        self.data = cog.data
        self.challonge = cog.challonge
        url = CHALLONGE_TOURNAMENT_URL_REGEX.search(text)
        if url:
            url = url.group("url")
            return await self.find(url=url)
        else:
            return await self.find(identifier=text)


class TournamentRegister:
    def __init__(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        tournament: challonge.Tournament,
        limit: int,
    ):
        self.ctx = ctx
        self.channel = channel
        self.tournament = tournament
        self.limit = limit
        self.data = ctx.bot.get_cog("TournamentManager").data
        self.current = 0
        self.participants = []
        self.loop: asyncio.AbstractEventLoop = ctx.bot.loop
        self.task: asyncio.Task = None
        self.message: discord.Message = None

    def add_participant(self, member: discord.Member):
        self.participants.append(member)
        self.current += 1

    async def write(self):
        await self.data.custom(TOURNAMENT, self.ctx.guild.id, self.tournament.id).participants.set(
            [x.id for x in self.participants]
        )

    def start_task(self):
        self.task = self.loop.create_task(self._register_update())

    def end_task(self):
        self.task.cancel()

    def _draw_line(self):
        # https://github.com/Cog-Creators/Red-DiscordBot/blob/V3/develop/redbot/cogs/audio/audio.py#L3920
        sections = 20
        progress = round((self.current / self.limit) * sections)
        bar = "="
        seek = ">"
        empty = " "
        msg = ""
        for i in range(sections):
            if i < progress:
                msg += bar
            elif i == progress:
                msg += seek
            else:
                msg += empty
        return f"`[{msg}]`"

    async def _register_update(self):
        embed = discord.Embed(title="Inscription des membres")
        embed.description = (
            f"L'inscription des membres est en cours dans le channel {self.channel.mention}\n"
            f"Ils doivent écrire `{CONFIRM_MESSAGE_REGEX.pattern}` (insensible à la casse)."
        )
        embed.add_field(name="\u200B", value="Initialisation...")
        embed.add_field(name="\u200B", value="\u200B")
        embed.colour = 0x00AA00
        self.message = await self.ctx.send(embed=embed)
        await self.message.add_reaction("❌")
        t1 = datetime.now()
        while True:
            await asyncio.sleep(1)
            time = datetime.now() - t1
            percent = round((self.current / self.limit) * 100, 2)
            embed.set_field_at(
                0,
                name="Progression",
                value=f"{self.current}/{self.limit} ({percent}%)\n{self._draw_line()}",
            )
            embed.set_field_at(1, name="Durée", value=time.strftime("%M:%S"))
            await self.message.edit(embed=embed)


class TournamentManager(commands.Cog):
    """
    Commands used for the Super Smash Bros. Ultimate tournament organization using Challonge.
    """

    default_guild = {
        "channels": {"register": None, "winner": None, "loser": None},
        "roles": {"participant": None},
        "blacklist": [],
        "current_tournament": None,
        "register": {"open": False, "limit": None, "current": 0},
    }
    default_tournament = {"participants": [], "url": None, "name": None, "major": False}

    def __init__(self, bot):
        self.bot: "Red" = bot
        self.data = Config.get_conf(self, 260, force_registration=True)
        self.challonge: challonge.User = None
        self.active_register = {}

        self.data.init_custom(TOURNAMENT, 2)
        self.data.register_guild(**self.default_guild)
        self.data.register_custom(TOURNAMENT, **self.default_tournament)

        self._init_logger()

    __version__ = "1.0.0"
    __author__ = ["retke (El Laggron)"]

    def _init_logger(self):
        log_format = logging.Formatter(
            f"%(asctime)s %(levelname)s {self.__class__.__name__}: %(message)s",
            datefmt="[%d/%m/%Y %H:%M]",
        )
        # logging to a log file
        # file is automatically created by the module, if the parent foler exists
        cog_path = cog_data_path(self)
        if cog_path.exists():
            log_path = cog_path / f"{os.path.basename(__file__)[:-3]}.log"
            file_handler = logging.FileHandler(log_path)
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(log_format)
            log.addHandler(file_handler)

        # stdout stuff
        stdout_handler = logging.StreamHandler()
        stdout_handler.setFormatter(log_format)
        # if --debug flag is passed, we also set our debugger on debug mode
        if logging.getLogger("red").isEnabledFor(logging.DEBUG):
            stdout_handler.setLevel(logging.DEBUG)
        else:
            stdout_handler.setLevel(logging.INFO)
        log.addHandler(stdout_handler)
        self.stdout_handler = stdout_handler

    async def _set_challonge(self):
        data = await self.bot.get_shared_api_tokens("challonge")
        username = data.get("username")
        token = data.get("token")
        if not any((username, token)):
            raise NotImplementedError("Username and/or token not set.")
        self.challonge = await challonge.get_user(username, token, loop=self.bot.loop)

    async def _add_roles(self, ctx: commands.Context, role: discord.Member, members: list):
        msg = None
        total = len(members)
        i = 0

        async def update_msg():
            while True:
                text = f"Ajout des rôles.... ({i}/{total} {round((i/total)*100, 2)}%)"
                if not msg:
                    msg = await ctx.send(text)
                else:
                    await msg.edit(content=text)
                await asyncio.sleep(1)

        task = self.bot.loop.create_task(update_msg())
        for member in members:
            try:
                await member.add_roles(
                    role, reason="Participation au tournoi, inscription manuelle/"
                )
            except discord.errors.HTTPException as e:
                log.error(
                    f"Impossible d'ajouter le role de participant à {member} ({member.id})",
                    exc_info=e,
                )
            else:
                i += 1
        task.cancel()
        text = f"Terminé ! ({i}/{total} {round((i/total)*100, 2)}%)"
        await msg.edit(content=text)

    @staticmethod
    async def _help_api_keys(ctx):
        await ctx.send(
            f"Aucun token enregistré. Utilisez `{ctx.clean_prefix}set api challonge "
            'username="votre nom d\'utilisateur" token="votre token"` puis réessayez.\n'
            "Obtenir une clé d'API: **https://challonge.com/settings/developer**"
        )

    @commands.group()
    @checks.admin()
    async def tournamentset(self, ctx):
        """
        Réglages des tournois.
        """
        pass

    @tournamentset.group(name="channel")
    async def tournamentset_channel(self, ctx):
        """
        Réglage des différents channels.
        """
        pass

    @tournamentset_channel.command(name="register")
    async def tournamentset_channel_register(self, ctx, *, channel: discord.TextChannel):
        """
        Règle le channel de participation.
        """
        guild = ctx.guild
        if not channel.permissions_for(guild.me).read_messages:
            await ctx.send("Je n'ai pas la permission de lire les messages dans ce channel.")
            return
        await self.data.guild(guild).channels.register.set(channel.id)
        await ctx.send(f"Le channel de participation est désormais {channel.mention}.")

    @tournamentset_channel.command(name="winnerbracket")
    async def tournamentset_channel_winnerbracket(self, ctx, *, channel: discord.TextChannel):
        """
        Règle le channel de participation.
        """
        guild = ctx.guild
        if not channel.permissions_for(guild.me).read_messages:
            await ctx.send("Je n'ai pas la permission de lire les messages dans ce channel.")
            return
        if not channel.permissions_for(guild.me).send_messages:
            await ctx.send("Je n'ai pas la permission d'envoyer des messages dans ce channel.")
            return
        if not channel.permissions_for(guild.me).embed_links:
            await ctx.send("Je n'ai pas la permission d'envoyer des liens dans ce channel.")
            return
        if not channel.permissions_for(guild.me).manage_messages:
            await ctx.send("Je n'ai pas la permission de gérer les messages dans ce channel.")
            return
        await self.data.guild(guild).channels.winner.set(channel.id)
        await ctx.send(
            "Le channel pour l'annonce des victoire du winner "
            f"bracket est désormais {channel.mention}."
        )

    @tournamentset_channel.command(name="loserbracket")
    async def tournamentset_channel_loserbracket(self, ctx, *, channel: discord.TextChannel):
        """
        Règle le channel de participation.
        """
        guild = ctx.guild
        if not channel.permissions_for(guild.me).read_messages:
            await ctx.send("Je n'ai pas la permission de lire les messages dans ce channel.")
            return
        if not channel.permissions_for(guild.me).send_messages:
            await ctx.send("Je n'ai pas la permission d'envoyer des messages dans ce channel.")
            return
        if not channel.permissions_for(guild.me).embed_links:
            await ctx.send("Je n'ai pas la permission d'envoyer des liens dans ce channel.")
            return
        if not channel.permissions_for(guild.me).manage_messages:
            await ctx.send("Je n'ai pas la permission de gérer les messages dans ce channel.")
            return
        await self.data.guild(guild).channels.loser.set(channel.id)
        await ctx.send(
            "Le channel pour l'annonce des victoire du loser "
            f"bracket est désormais {channel.mention}."
        )

    @tournamentset.command(name="role")
    async def tournamentset_role(self, ctx, *, role: discord.Role):
        """
        Règle le role de participant.
        """
        guild = ctx.guild
        if role.position >= guild.me.top_role.position:
            await ctx.send(
                "Ce role est trop haut dans la hiérarchie "
                "pour que je puisse l'assigner aux membres."
            )
            return
        await self.data.guild(guild).roles.participant.set(role.id)
        await ctx.send("Rôle enregistré.")

    @commands.group()
    async def tournament(self, ctx):
        """
        Gestion des tournois
        """
        pass

    @tournament.command(name="create")
    async def tournament_create(self, ctx, index: int = None, major: bool = False):
        """
        Crée un nouveau tournoi.
        """
        guild = ctx.guild
        if index is None:
            index = (
                len(
                    list(
                        filter(
                            lambda x: x["major"] is major,
                            await self.data.guild(guild).tournaments(),
                        )
                    )
                )
                + 1
            )
        msg = await ctx.send(
            f"Création du tournoi {'MAJOR ' if major else ''}#{index}, continuer ?"
        )
        menus.start_adding_reactions(msg, menus.ReactionPredicate.YES_OR_NO_EMOJIS)
        pred = menus.ReactionPredicate.yes_or_no(msg, ctx.author)
        try:
            await self.bot.wait_for("reaction_add", check=pred, timeout=20)
        except asyncio.TimeoutError:
            await ctx.send("Annulation de la création du tournoi.")
            return
        if not pred.result:
            await ctx.send("Annulation de la création du tournoi.")
            return
        try:
            await self._set_challonge()
        except NotImplementedError:
            await self._help_api_keys(ctx)
            return
        try:
            tournament: challonge.Tournament = await self.challonge.create_tournament(
                name=TOURNAMENT_NAME.format(index=index, major="MAJOR " if major else ""),
                url=TOURNAMENT_URL.format(index=index),
                tournament_type=challonge.TournamentType.double_elimination,
                **TOURNAMENT_PARAMETERS,
            )
        except challonge.APIException as e:
            log.error(f"Cannot create tournament {index}", exc_info=e)
            await ctx.send(
                "Une erreur s'est produite lors de la création du tournoi, regardez "
                "les logs pour plus d'informations."
            )
            return
        else:
            await self.data.custom(TOURNAMENT, guild.id, tournament.id).url.set(tournament.url)
            await self.data.custom(TOURNAMENT, guild.id, tournament.id).name.set(tournament.name)
            await self.data.custom(TOURNAMENT, guild.id, tournament.id).major.set(major)
            await ctx.send(
                f"Tournoi {'majeur ' if major else ''}créé avec succès : "
                f"{tournament.full_challonge_url} (ID: {tournament.id})"
            )

    @tournament.command(name="add")
    async def tournament_add(self, ctx, tournament_url: str, major: bool = False):
        """
        Ajoute un tournoi existant dans la liste.
        """
        guild = ctx.guild
        match = CHALLONGE_TOURNAMENT_URL_REGEX.match(tournament_url)
        if not match:
            await ctx.send("URL invalide. Il doit être de la forme `https://challonge.com/XXX`")
            return
        url = match.group("url")
        try:
            await self._set_challonge()
        except NotImplementedError:
            await self._help_api_keys(ctx)
            return
        try:
            tournament: challonge.Tournament = await self.challonge.get_tournament(url=url)
        except challonge.APIException as e:
            if e.args[1] == 404:
                await ctx.send("Tournoi non trouvé.")
            else:
                log.error(f"Cannot find tournament {tournament_url}.", exc_info=e)
                await ctx.send(
                    "Une erreur s'est produite lors de la recherche du tournoi, regardez "
                    "les logs pour plus d'informations."
                )
            return
        else:
            await self.data.custom(TOURNAMENT, guild.id, tournament.id).url.set(tournament.url)
            await self.data.custom(TOURNAMENT, guild.id, tournament.id).name.set(tournament.name)
            await self.data.custom(TOURNAMENT, guild.id, tournament.id).major.set(major)
            await ctx.send(f"Tournoi ajouté avec succès (ID: {tournament.id})")

    @tournament.command(name="list")
    async def tournament_list(self, ctx):
        """
        Liste tous les tournois enregistrés.
        """
        guild = ctx.guild
        try:
            await self._set_challonge()
        except NotImplementedError:
            await self._help_api_keys(ctx)
            return
        data = await self.data.custom(TOURNAMENT, guild.id).all()
        if not data:
            await ctx.send("Aucun tournoi enregistré.")
            return
        to_remove = []
        tournaments = []
        major_tournaments = []
        async with ctx.typing():
            for tournament, data in data.items():
                try:
                    tournament_object = await self.challonge.get_tournament(t_id=tournament)
                except challonge.APIException as e:
                    to_remove.append(tournament)
                    log.warn(
                        f"Will remove tournament with ID {tournament} because of an exception",
                        exc_info=e,
                    )
                else:
                    if data["major"]:
                        major_tournaments.append(tournament_object)
                    else:
                        tournaments.append(tournament_object)
        if to_remove:
            async with self.data.custom(TOURNAMENT, guild.id).all() as data:
                data = {x: y for x, y in data.items() if x not in to_remove}
        text = ""
        if tournaments:
            text += "**Liste des tournois (weekly) enregistrés**\n\n"
            for tournament in tournaments:
                text += (
                    f"`{tournament.id}`: {tournament.name}  *<{tournament.full_challonge_url}>*\n"
                )
        if major_tournaments:
            text += "\n\n**Liste des tournois (major) enregistrés**\n\n"
            for tournament in major_tournaments:
                text += (
                    f"`{tournament.id}`: {tournament.name}  *<{tournament.full_challonge_url}>*\n"
                )
        for page in pagify(text, delims=["\n\n", "\n"], priority=True):
            await ctx.send(page)

    @tournament.command(name="remove", aliases=["del", "delete"])
    async def tournament_remove(self, ctx, tournament: Union[TournamentConverter, str]):
        """
        Retire un tournoi de la liste.

        Vous devez donner l'ID du tournoi indiqué avec la commande `[p]tournament list`.
        """
        guild = ctx.guild
        try:
            await self._set_challonge()
        except NotImplementedError:
            await self._help_api_keys(ctx)
            return
        tournaments = await self.data.custom(TOURNAMENT, guild.id).all()
        if isinstance(tournament, str):
            to_delete = tournaments.get(tournament)
            if not to_delete:
                await ctx.send("Tournoi non trouvé.")
                return
            await ctx.send(
                "Impossible de retrouver le tournoi séléctionné sur Challonge, "
                "Il va être retiré."
            )
            await self.data.custom(TOURNAMENT, guild.id).clear_raw(tournament)
            return
        msg = await ctx.send(
            f"Suppression du tournoi {tournament.name} *{tournament.full_challonge_url}\n"
            "Continuer ?"
        )
        menus.start_adding_reactions(msg, menus.ReactionPredicate.YES_OR_NO_EMOJIS)
        pred = menus.ReactionPredicate.yes_or_no(ctx, ctx.author)
        try:
            await self.bot.wait_for("reaction_add", check=pred, timeout=20)
        except asyncio.TimeoutError:
            await ctx.send("Annulation.")
            return
        if not pred.result:
            await ctx.send("Annulation.")
            return
        await self.data.custom(TOURNAMENT, guild.id).clear_raw(tournament.id)
        log.info(f"Removed tournament {tournament.url}")
        await ctx.send("Tournoi supprimé avec succès.")

    @tournament.command(name="register")
    async def tournamentset_register(
        self, ctx: commands.Context, tournament: TournamentConverter, limit: int
    ):
        """
        Démarre l'inscription au prochain tournoi.
        """
        guild = ctx.guild
        if limit <= 0:
            await ctx.send("Limite invalide.")
            return
        channel: discord.TextChannel = guild.get_channel(
            await self.data.guild(guild).channels.register()
        )
        if not channel:
            await ctx.send(
                "Le channel d'inscriptions n'est pas réglé, utilisez "
                f"`{ctx.clean_prefix}tournamentset channel register`."
            )
            return
        msg = await ctx.send(
            f"Inscription au tournoi {tournament.name} ({tournament.full_challonge_url}).\n"
            f"- Nombre maximum de participants : {limit}"
            f"\n- Channel : {channel.mention}\n\nContinuer ?"
        )
        menus.start_adding_reactions(msg, menus.ReactionPredicate.YES_OR_NO_EMOJIS)
        pred = menus.ReactionPredicate.yes_or_no(msg, ctx.author)
        try:
            await self.bot.wait_for("reaction_add", check=pred, timeout=20)
        except asyncio.TimeoutError:
            await ctx.send("Annulation.")
            return
        if not pred.result:
            await ctx.send("Annulation.")
            return
        n = TournamentRegister(ctx, channel, tournament, limit)
        self.active_register[guild.id] = n
        await ctx.send("Les inscriptions sont ouvertes !")
        if channel.id != ctx.channel.id:
            await channel.send("Les inscriptions sont ouvertes !")
        n.start_task()
        try:
            await channel.set_permissions(
                guild.default_role,
                reason="Ouverture des inscriptions",
                send_messages=None,
                read_messages=False,
            )
        except discord.HTTPException as e:
            await ctx.send(
                "Il y a eu une erreur lors de l'ouverture du channel, mais le bot est prêt à "
                "recevoir les inscriptions. Ouvrez le channel manuellement."
            )
            log.error("Error when opening the channel", exc_info=e)

    @tournament.command(name="manualregister")
    async def tournament_manualregister(
        self,
        ctx: commands.Context,
        tournament: TournamentConverter,
        channel: discord.TextChannel,
        limit: int,
        after: discord.Message = None,
    ):
        """
        Enregistrement manuel des membres avec l'historique des messages d'un channel.

        `<limit>` est le nombre maximum de participants à enregister.
        """
        guild = ctx.guild
        i = 0
        participant = guild.get_role(await self.data.guild(guild).roles.participant())
        participants = []
        async with ctx.typing():
            async for message in channel.history(limit=1000, oldest_first=True, after=after):
                member = message.member
                if participant in member.roles:
                    continue
                if member in participants:
                    continue
                if member.id in await self.data.guild(guild).blacklist():
                    continue
                if not CONFIRM_MESSAGE_REGEX.match(message.content.lower()):
                    continue
                participants.append(member)
                i += 1
                if i >= limit:
                    break
        msg = await ctx.send(f"{len(participants)} membres trouvés. Attribuer les rôles ?")
        pred = menus.ReactionPredicate().yes_or_no(msg, ctx.author)
        menus.start_adding_reactions(msg, menus.ReactionPredicate.YES_OR_NO_EMOJIS)
        try:
            await self.bot.wait_for("reaction_add", check=pred, timeout=30)
        except asyncio.TimeoutError:
            await ctx.send("Demande expirée.")
            return
        if not pred.result:
            await ctx.send("Annulation...")
            return
        await self._add_roles(ctx, participant, participants)

    @tournament.command(name="seeding")
    async def tournament_seeding(
        self, ctx: commands.Context, tournament: TournamentConverter, encoding: str = "utf-8"
    ):
        """
        Donnez le fichier de participants "seedé" pour l'envoyer sur Challonge.
        """
        guild = ctx.guild
        if not ctx.message.attachments:
            await ctx.send("Vous devez envoyer le fichier de partipants avec la commande.")
            return
        file: discord.Attachment = ctx.message.attachments[0]
        content: bytes = file.read(use_cached=True)
        content.decode(encoding=encoding, errors="replace")
        participants = content.splitlines()
        async with ctx.typing():
            try:
                tournament: challonge.Tournament = await self.challonge.get_tournament(
                    t_id=tournament
                )
            except challonge.APIException as e:
                to_remove.append(tournament)
                log.warn(
                    f"Will remove tournament with ID {tournament} because of an exception",
                    exc_info=e,
                )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        guild = message.guild
        if guild.id in self.active_register:
            await self.process_register_message(message)

    async def process_register_message(self, message: discord.Message):
        guild = message.guild
        member = message.author
        if message.channel.id != await self.data.guild(guild).channels.register():
            return
        participant = guild.get_role(await self.data.guild(guild).roles.participant())
        if participant in member.roles:
            return
        if member.id in await self.data.guild(guild).blacklist():
            return
        if not CONFIRM_MESSAGE_REGEX.match(message.content.lower()):
            return
        register = self.active_register[guild.id]
        if member in register.participants:
            return
        register.add_participant(member)
        if register.current >= register.limit:
            await self.close_register(guild)

    async def close_register(self, guild: discord.Guild):
        register: TournamentRegister = self.active_register[guild.id]
        ctx = register.ctx
        await register.write()
        await ctx.channel.set_permissions(
            guild.default_role, reason="Fermeture des inscriptions", send_messages=False
        )
        register.end_task()
        participant = guild.get_role(await self.data.guild(guild).roles.participant())
        file = text_to_file("\n".join([str(x) for x in register.participants]))
        if participant is None:
            await ctx.send(
                "Impossible de trouver le role de participant, "
                "il n'y a donc eu aucune attribution de role.",
                file=file,
            )
            del self.active_register[guild.id]
            return
        await ctx.send(f"{len(register.participants)} membres enregistrés.")
        await self._add_roles(ctx, participant, register.participants)
        del self.active_register[guild.id]

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.Member):
        if reaction.emoji != "❌":
            return
        try:
            guild = user.guild
        except AttributeError:
            return
        try:
            register: TournamentRegister = self.active_register[guild.id]
        except KeyError:
            return
        if user.id != register.ctx.author.id:
            return
        if reaction.message.id == register.message.id:
            await self.close_register(guild)

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if not isinstance(error, commands.CommandInvokeError):
            return
        if not ctx.command.cog_name == self.__class__.__name__:
            # That error doesn't belong to the cog
            return
        log.removeHandler(self.stdout_handler)  # remove console output since red also handle this
        log.error(
            f"Exception in command '{ctx.command.qualified_name}'.\n\n", exc_info=error.original
        )
        log.addHandler(self.stdout_handler)  # re-enable console output for warnings

    def cog_unload(self):
        log.debug("Unloading cog...")

        # remove all handlers from the logger, this prevents adding
        # multiple times the same handler if the cog gets reloaded
        log.handlers = []

        for tournament in self.active_register.values():
            tournament.end_task()
