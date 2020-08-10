import discord
import asyncio
import re
import json
import logging

from typing import Optional
from datetime import datetime, timedelta

from redbot.core import commands
from redbot.core import Config
from redbot.core import checks
from redbot.core.bot import Red
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import ReactionPredicate
from redbot.core.utils.chat_formatting import text_to_file, pagify

from .progress_menu import UpdateRoles, Inscription, CheckIn

MESSAGE_CHECK = re.compile(r"^je participe\.?$", flags=re.I)
log = logging.getLogger("red.laggron.tournamentmanager")


class UserInputError(Exception):
    pass


class TournamentManager(commands.Cog):
    """
    Gère l'inscription aux tournois de Bronol.
    """

    default_guild = {
        "roles": {"participant": None, "tournament": None, "check": None,},
        "channels": {"inscription": None, "check": None,},
        "next_to_blacklist": [],  # members who didn't check, will be blacklisted at the end
        "blacklisted": [],
        "current": [],
        "check_time": 1800
    }

    def __init__(self, bot: Red):
        self.bot = bot

        self.data = Config.get_conf(self, 260)
        self.data.register_guild(**self.default_guild)

        # cache
        self.participant_roles = {}
        self.checkin_roles = {}
        self.inscription_channels = {}

    async def _ask_for(
        self,
        ctx: commands.Context,
        message: discord.Message,
        author: discord.User = None,
        timeout: int = 20,
    ):
        pred = ReactionPredicate.yes_or_no(message, author or ctx.author)
        start_adding_reactions(message, ReactionPredicate.YES_OR_NO_EMOJIS)
        try:
            await self.bot.wait_for("reaction_add", check=pred, timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return pred.result

    async def get_participant_role(self, guild: discord.Guild) -> discord.Role:
        role_id = self.participant_roles.get(guild.id)
        if not role_id:
            role_id = await self.data.guild(guild).roles.participant()
            if not role_id:
                raise UserInputError("Le rôle de participant n'est pas réglé.")
            self.participant_roles[guild.id] = role_id
        role = guild.get_role(role_id)
        if not role:
            raise UserInputError("Le rôle de participant a été perdu.")
        return role

    async def get_checkin_role(self, guild: discord.Guild) -> discord.Role:
        role_id = self.checkin_roles.get(guild.id)
        if not role_id:
            role_id = await self.data.guild(guild).roles.check()
            if not role_id:
                raise UserInputError("Le rôle de check-in n'est pas réglé.")
            self.checkin_roles[guild.id] = role_id
        role = guild.get_role(role_id)
        if not role:
            raise UserInputError("Le rôle de check-in a été perdu.")
        return role

    async def get_tournament_role(self, guild: discord.Guild) -> discord.Role:
        # no cache for this one
        role_id = await self.data.guild(guild).roles.tournament()
        if not role_id:
            raise UserInputError("Le rôle de tournois n'est pas réglé.")
        role = guild.get_role(role_id)
        if not role:
            raise UserInputError("Le rôle de tournois a été perdu.")
        return role

    async def get_channel(self, guild: discord.Guild) -> discord.TextChannel:
        channel_id = self.inscription_channels.get(guild.id)
        if not channel_id:
            channel_id = await self.data.guild(guild).channels.inscription()
            if not channel_id:
                raise UserInputError("Le channel d'inscriptions n'est pas réglé.")
            self.inscription_channels[guild.id] = channel_id
        channel = guild.get_channel(channel_id)
        if not channel:
            raise UserInputError("Le channel d'inscriptions a été perdu.")
        return channel

    async def get_checkin_channel(self, guild: discord.Guild) -> discord.TextChannel:
        # no cache here too
        channel_id = await self.data.guild(guild).channels.check()
        if not channel_id:
            raise UserInputError("Le channel de check-in n'est pas réglé.")
        channel = guild.get_channel(channel_id)
        if not channel:
            raise UserInputError("Le channel de check-in a été perdu.")
        return channel

    @commands.group()
    @checks.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def tournamentset(self, ctx: commands.Context):
        """
        Règlages du module.
        """
        pass

    @tournamentset.command(name="participant")
    async def tournamentset_participant(self, ctx: commands.Context, *, role: discord.Role):
        """
        Définis le rôle de participant.
        """
        if role.position >= ctx.guild.me.top_role.position:
            await ctx.send("Ce rôle est au dessus de mon rôle, je ne peux donc pas l'assigner.")
            return
        await self.data.guild(ctx.guild).roles.participant.set(role.id)
        await ctx.send("Rôle configuré!")

    @tournamentset.command(name="tournoi")
    async def tournamentset_tournoi(self, ctx: commands.Context, *, role: discord.Role):
        """
        Définis le rôle de tournois.
        """
        if role.position >= ctx.guild.me.top_role.position:
            await ctx.send("Ce rôle est au dessus de mon rôle, je ne peux donc pas l'assigner.")
            return
        await self.data.guild(ctx.guild).roles.tournament.set(role.id)
        await ctx.send("Rôle configuré!")

    @tournamentset.command(name="inscription")
    async def tournamentset_inscription(
        self, ctx: commands.Context, *, channel: discord.TextChannel
    ):
        """
        Définis le channel d'insciptions.
        """
        overwrite = channel.permissions_for(ctx.guild.me)
        if overwrite.read_messages is False or overwrite.manage_channels is False:
            await ctx.send(
                "J'ai besoin de la permission de lire les messages et d'éditer ce channel."
            )
            return
        await self.data.guild(ctx.guild).channels.inscription.set(channel.id)
        await ctx.send("Channel configuré!")

    @tournamentset.command(name="checkin")
    async def tournamentset_checkin(self, ctx: commands.Context, *, channel: discord.TextChannel):
        """
        Définis le channel de check-in.
        """
        overwrite = channel.permissions_for(ctx.guild.me)
        if overwrite.read_messages is False or overwrite.manage_channels is False:
            await ctx.send(
                "J'ai besoin de la permission de lire les messages et d'éditer ce channel."
            )
            return
        await self.data.guild(ctx.guild).channels.check.set(channel.id)
        await ctx.send("Channel configuré!")

    @tournamentset.command(name="checkinrole")
    async def tournamentset_checkinrole(self, ctx: commands.Context, *, role: discord.Role):
        """
        Définis le rôle de check-in.
        """
        if role.position >= ctx.guild.me.top_role.position:
            await ctx.send("Ce rôle est au dessus de mon rôle, je ne peux donc pas l'assigner.")
            return
        await self.data.guild(ctx.guild).roles.check.set(role.id)
        await ctx.send("Rôle configuré!")

    @tournamentset.command(name="settings")
    async def tournamentset_settings(self, ctx: commands.Context):
        """
        Affiche les réglages enregistrés du module.
        """
        guild = ctx.guild
        roles = await self.data.guild(guild).roles.all()
        channels = await self.data.guild(guild).channels.all()
        participants = len(await self.data.guild(guild).current())
        blacklisted = len(await self.data.guild(guild).blacklisted())
        check_time = await self.data.guild(guild).check_time()
        roles_description = ""
        channels_description = ""
        for key, role_id in roles.items():
            role = guild.get_role(role_id)
            if role:
                roles_description += f"{key}: {role.name} ({role.id})\n"
            else:
                roles_description += f"{key}: Non défini\n"
        for key, channel_id in channels.items():
            channel = guild.get_channel(channel_id)
            if channel:
                channels_description += f"{key}: {channel.mention} ({channel.id})\n"
            else:
                channels_description += f"{key}: Non défini\n"
        embed = discord.Embed()
        embed.colour = 0xE8C15F
        embed.description = "Réglages du module de gestion de tournois."
        embed.add_field(name="Rôles", value=roles_description, inline=False)
        embed.add_field(name="Channels", value=channels_description, inline=False)
        embed.add_field(name="durée du check in", value=check_time, inline=False)
        embed.add_field(
            name="Participants", value=f"{participants} membres enregistrés", inline=True
        )

        embed.add_field(name="Blacklist", value=f"{blacklisted} membres blacklistés", inline=True)
        embed.set_footer(
            text=(
                f'Taper "{ctx.clean_prefix}help tournamentset" '
                "pour la liste des commandes de configuration."
            )
        )
        await ctx.send(embed=embed)

    @tournamentset.command(name="checkintime")
    async def tournamentset_checkintime(self, ctx: commands.Context, duree: int):
        """règle la durée du check in. Doit-être en minutes."""

        if duree > 10080:
            await ctx.send("durée trop longue. Doit être inferieur a 1 semaine.")
            return
        await self.data.guild(ctx.guild).check_time.set(duree * 60)
        await ctx.send("durée reglée")

    @commands.group()
    @checks.admin()
    async def tournamentban(self, ctx: commands.Context):
        """
        Gère les banissements des tournois.
        """
        pass

    @tournamentban.command(name="add")
    async def tournamentban_add(self, ctx: commands.Context, *, member: discord.Member):
        """
        Empêche un membre de participer au prochain tournoi.
        """
        guild = ctx.guild
        async with self.data.guild(guild).blacklisted() as blacklist:
            blacklist.append(member.id)
        text = "Membre blacklisté pour le prochain tournoi."
        try:
            role = await self.get_participant_role(guild)
        except UserInputError:
            pass
        else:
            if role in member.roles:
                await member.remove_roles(role, reason="Membre blacklisté")
                text += "\nSon rôle de participant a également été retiré."
        await ctx.send(text)

    @tournamentban.command(name="remove")
    async def tournamentban_remove(self, ctx: commands.Context, *, member: discord.Member):
        """
        Retire le ban d'un membre.
        """
        guild = ctx.guild
        async with self.data.guild(guild).blacklisted() as blacklist:
            try:
                blacklist.remove(member.id)
            except KeyError:
                await ctx.send("Le membre n'est pas dans la blacklist.")
            else:
                await ctx.send("Le membre n'est plus banni.")

    @tournamentban.command(name="list")
    async def tournamentban_list(self, ctx: commands.Context):
        """
        Liste tous les membres bannis du prochain tournoi.
        """
        guild = ctx.guild
        text = "Liste des membres bannis:\n\n"
        async with self.data.guild(guild).blacklisted() as blacklist:
            for member_id in blacklist:
                member = guild.get_member(member_id)
                if member:
                    text += f"- {str(member)} ({member.id})\n"
                else:
                    text += f"- {member_id} (le membre n'est plus sur le serveur)\n"
        for page in pagify(text):
            await ctx.send(page)

    @tournamentban.command(name="clear")
    async def tournamentban_clear(self, ctx: commands.Context):
        """
        Nettoie la liste des membres bannis.
        """
        guild = ctx.guild
        await self.data.guild(guild).blacklisted.set([])
        await ctx.send("La blacklist a été réinitialisée.")

    @commands.command()
    @checks.mod()
    @commands.guild_only()
    async def inscription(self, ctx: commands.Context, limit: int):
        """
        Lance l'inscription pour le tournoi avec la limite de participants donnée.
        """
        guild = ctx.guild
        try:
            role = await self.get_tournament_role(guild)
            participant_role = await self.get_participant_role(guild)
            channel = await self.get_channel(guild)
        except UserInputError as e:
            await ctx.send(e.args[0])
            return
        if not ctx.assume_yes:
            message = await ctx.send(
                f"Channel d'inscription: {channel.mention}\n"
                f"Role de tournois: {role.name}\n"
                f"Limite de participants: {limit}\n\n"
                f"Lancer l'inscription ?"
            )
            result = await self._ask_for(ctx, message)
            if result is False:
                await ctx.send("Annulation.")
                return
            await message.delete()
        await self.data.guild(guild).current.set([])
        blacklist = await self.data.guild(guild).blacklisted()
        n = Inscription(
            self.bot, self.data, ctx, limit, channel, role, participant_role, blacklist
        )
        await n.run()

    @commands.command()
    @checks.mod()
    async def valid(self, ctx: commands.Context, number: int):
        """
        Valide un certain nombre de membres pour l'inscription.
        """
        guild = ctx.guild
        participants = await self.data.guild(guild).current()
        participants = list(filter(None, [guild.get_member(x) for x in participants]))
        total = len(participants)
        if number > total:
            await ctx.send(
                f"La limite est plus élevée que le nombre de joueurs retenus ({total})."
            )
            return
        try:
            role = await self.get_participant_role(guild)
        except UserInputError as e:
            await ctx.send(e.args[0])
            return
        message = await ctx.send(
            f"Ajouter le rôle de participant ({role.name}) à {number} membres ?"
        )
        result = await self._ask_for(ctx, message)
        if result is False:
            await ctx.send("Annulation.")
            return
        n = UpdateRoles(self.bot, ctx, participants[:number], [role], "Participation au tournoi.")
        await n.run()

    @commands.command()
    @checks.mod()
    async def manualregister(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        limit: int,
        after: discord.Message = None,
    ):
        """
        Effecture un enregistrement manuel en cas de problème.

        Vous devez donner les éléments suivants dans l'ordre :
        - `channel` Le channel où a eu lieu l'inscription
        - `limit` Le nombre de participants maximum à inscrire
        - `after` (Optionel) Le lien vers le message à partir duquel il faut vérifier les messages
        """
        guild = ctx.guild
        if not channel.permissions_for(guild.me).read_message_history:
            await ctx.send("Je ne peux pas lire l'historique des messages dans ce channel.")
            return
        participants = []
        if after:
            after = after.created_at
        blacklist = await self.data.guild(guild).blacklisted()
        await self.data.guild(guild).current.set([])
        async with ctx.typing():
            async for message in channel.history(oldest_first=True, after=after):
                if not MESSAGE_CHECK.match(message.content):
                    return
                member = message.author
                if member.id in blacklist:
                    return
                participants.append(member)
                if len(participants) >= limit:
                    break
        if len(participants) < limit:
            await ctx.send(f"Pas assez de participants trouvés ({len(participants)}/{limit})")
            return
        await self.data.guild(guild).current.set(participants)
        await ctx.send(
            f"Inscription terminée, {len(participants)} membres enregistrés. Envoi du fichier..."
        )
        async with ctx.typing():
            content = "\n".join((str(guild.get_member(x)) for x in participants))
            file = text_to_file(content, "participants.txt")
            await ctx.send(file=file)

    @commands.command()
    @checks.mod()
    async def tinfo(self, ctx: commands.Context):
        """
        Affiche diverses informations liées au tournoi.
        """
        guild = ctx.guild
        participants = await self.data.guild(guild).current()
        blacklisted = await self.data.guild(guild).blacklisted()
        text = (
            "__Informations sur le tournoi__\n"
            f"- Nombre de participants enregistrés : **{len(participants)}**\n"
            f"- Nombre de membres blacklistés : **{len(blacklisted)}**\n\n"
            "__Informations sur les roles__\n"
        )
        try:
            tournament_role = await self.get_tournament_role(guild)
        except UserInputError:
            pass
        else:
            text += (
                f"- Nombre de membres avec le rôle {tournament_role.name} "
                f": **{len(tournament_role.members)}**\n"
            )
        try:
            participant_role = await self.get_participant_role(guild)
        except UserInputError:
            pass
        else:
            text += (
                f"- Nombre de membres avec le rôle {participant_role.name} "
                f": **{len(participant_role.members)}**\n"
            )
        try:
            check_role = await self.get_checkin_role(guild)
        except UserInputError:
            pass
        else:
            text += (
                f"- Nombre de membres avec le rôle {check_role.name} "
                f": **{len(check_role.members)}**\n"
            )
        await ctx.send(text)

    @commands.command(name="list")
    @checks.mod()
    async def _list(self, ctx: commands.Context):
        """
        Génère les listes de participants à partir du rôle défini.
        """
        guild = ctx.guild
        try:
            role = await self.get_participant_role(guild)
        except UserInputError as e:
            await ctx.send(e.args[0])
            return
        async with ctx.typing():
            text = "Liste des membres avec le rôle participant:\n\n"
            for member in role.members:
                text += str(member) + "\n"
            text += f"\nNombre de participants : {len(role.members)}"
            file = text_to_file(text, filename="participants.txt")
            await ctx.send(
                f"Liste des {len(role.members)} membres participants.", file=file,
            )

    @commands.command()
    @checks.mod()
    async def startcheck(self, ctx: commands.Context):
        """
        Démarre la phase de check pendant la durée définie.
        """
        guild = ctx.guild
        check_time = await self.data.guild(ctx.guild).check_time()
        try:
            check_role = await self.get_checkin_role(guild)
            participant_role = await self.get_participant_role(guild)
            channel = await self.get_checkin_channel(guild)
        except UserInputError as e:
            await ctx.send(e.args[0])
            return
        total = len(participant_role.members)
        if total < 1:
            await ctx.send(f"Aucun membre n'a le rôle {participant_role.name} !")
            return
        if not ctx.assume_yes:
            message = await ctx.send(
                f"Channel de check-in: {channel.mention}\n"
                f"Role de check-in: {check_role.name}\n"
                f"Nombre de participants: {total}\n\n"
                "Lancer le check ?"
            )
            result = await self._ask_for(ctx, message)
            if result is False:
                await ctx.send("Annulation.")
                return
            await message.delete()
        n = CheckIn(self.bot, self.data, ctx, channel, check_role, participant_role, check_time)
        await n.run()
        try:
            await n.update_message_task
        except asyncio.CancelledError:
            pass
        try:
            await n.cancel_task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(1)
        await ctx.send(f"Retrait du rôle {participant_role.name} aux membres non checks...")
        n = UpdateRoles(
            self.bot, ctx, n.to_blacklist, [participant_role], "Membre non check", add_roles=False
        )
        await n.run()

    @commands.command()
    @checks.mod()
    async def endtournament(self, ctx: commands.Context):
        """
        Met fin au tournoi actuel.
        
        Cette commande fait les actions suivantes :
        - Retrait des rôles de participants et de check à tous les membres
        - Réinitialisation de la blacklist
        - Ajout des membres non checks (qui ne se sont pas inscrits entre temps) à la blacklist
        """
        guild = ctx.guild
        try:
            check_role = await self.get_checkin_role(guild)
            participant_role = await self.get_participant_role(guild)
        except UserInputError as e:
            await ctx.send(e.args[0])
            return
        next_to_blacklist = await self.data.guild(guild).next_to_blacklist()
        if not ctx.assume_yes:
            message = await ctx.send(
                "Cette commande va exécuter les actions suivantes :\n"
                f'- Retrait des rôles "{participant_role.name}" et "{check_role.name}" à tous '
                f"les membres ({len(participant_role.members)} membres)\n"
                "- Réinitialisation de la blacklist\n"
                f"- Ajout des {len(next_to_blacklist)} membres n'ayant pas check et ne s'étant "
                "pas inscrit entre temps à la blacklist\n\n"
                "Continuer ?"
            )
            result = await self._ask_for(ctx, message)
            if result is False:
                await ctx.send("Annulation...")
                return
            await message.delete()
        members = check_role.members.copy()
        members.extend(x for x in participant_role.members if x not in check_role.members)
        n = UpdateRoles(
            self.bot,
            ctx,
            members,
            [check_role, participant_role],
            reason="Fin du tournoi",
            add_roles=False,
        )
        await n.run()
        try:
            await n.update_message_task
        except asyncio.CancelledError:
            pass
        try:
            await n.cancel_task
        except asyncio.CancelledError:
            pass
        await self.data.guild(guild).blacklisted.set(next_to_blacklist)
        await self.data.guild(guild).next_to_blacklist.set([])
        text = "Blacklist réinitialisée.\n"
        if next_to_blacklist:
            if len(next_to_blacklist) == 1:
                text += "Le membre n'ayant pas check a été ajouté à la blacklist.\n"
            else:
                text += (
                    f"Les {len(next_to_blacklist)} membres n'ayant pas "
                    "check ont été ajoutés à la blacklist.\n"
                )
            text += f"Tapez `{ctx.clean_prefix}tournamentban list` pour voir la blacklist."
        await ctx.send(text)
