import discord
import asyncio
import re
import json

from typing import Optional
from datetime import timedelta

from redbot.core import commands
from redbot.core import Config
from redbot.core import checks
from redbot.core.bot import Red
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import ReactionPredicate
from redbot.core.utils.chat_formatting import text_to_file, pagify

NAME_CHECK = re.compile(r"^[\w ]{,32}$")
MESSAGE_CHECK = re.compile(r"^je participe\.?$", flags=re.I)


class UserInputError(Exception):
    pass


class TournamentManager(commands.Cog):
    """
    Gère l'inscription aux tournois de Bronol.
    """

    default_guild = {
        "roles": {
            "participant": None,
            "tournament": None,
            "check": None,
        },
        "channels": {
            "inscription": None,
            "vip_inscription": None,
            "check": None,
        },
        "blacklisted": [],
        "current": [],
    }

    def __init__(self, bot: Red):
        self.bot = bot

        self.data = Config.get_conf(self, 260)
        self.data.register_guild(**self.default_guild)

        # cache
        self.participant_roles = {}
        self.checkin_roles = {}
        self.inscription_channels = {}
        self.vip_inscription_channels = {}

    async def _ask_for(self, ctx: commands.Context, message: discord.Message, timeout: int = 20):
        pred = ReactionPredicate.yes_or_no(message, ctx.author)
        start_adding_reactions(message, ReactionPredicate.YES_OR_NO_EMOJIS)
        try:
            await self.bot.wait_for("reaction_add", check=pred, timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return pred.result

    async def get_participant_role(self, guild: discord.Guild) -> discord.Role:
        role = self.participant_roles.get(guild.id)
        if role:
            return role
        role_id = await self.data.guild(guild).roles.participant()
        if not role_id:
            raise UserInputError("Le rôle de participant n'est pas réglé.")
        role = guild.get_role(role_id)
        if not role:
            raise UserInputError("Le rôle de participant a été perdu.")
        self.participant_roles[guild.id] = role
        return role

    async def get_checkin_role(self, guild: discord.Guild) -> discord.Role:
        role = self.checkin_roles.get(guild.id)
        if role:
            return role
        role_id = await self.data.guild(guild).roles.check()
        if not role_id:
            raise UserInputError("Le rôle de check-in n'est pas réglé.")
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
        channel = self.inscription_channels.get(guild.id)
        if channel:
            return channel
        channel_id = await self.data.guild(guild).channels.inscription()
        if not channel_id:
            raise UserInputError("Le channel d'inscriptions n'est pas réglé.")
        channel = guild.get_channel(channel_id)
        if not channel:
            raise UserInputError("Le channel d'inscriptions a été perdu.")
        self.inscription_channels[guild.id] = channel
        return channel

    async def get_vip_channel(self, guild: discord.Guild) -> discord.TextChannel:
        channel = self.vip_inscription_channels.get(guild.id)
        if channel:
            return channel
        channel_id = await self.data.guild(guild).channels.vip_inscription()
        if not channel_id:
            raise UserInputError("Le channel d'inscriptions n'est pas réglé.")
        channel = guild.get_channel(channel_id)
        if not channel:
            raise UserInputError("Le channel d'inscriptions a été perdu.")
        self.vip_inscription_channels[guild.id] = channel
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

    @tournamentset.command(name="inscriptionvip")
    async def tournamentset_inscriptionvip(
        self, ctx: commands.Context, *, channel: discord.TextChannel
    ):
        """
        Définis le channel d'insciptions VIP.
        """
        overwrite = channel.permissions_for(ctx.guild.me)
        if overwrite.read_messages is False or overwrite.manage_channels is False:
            await ctx.send(
                "J'ai besoin de la permission de lire les messages et d'éditer ce channel."
            )
            return
        await self.data.guild(ctx.guild).channels.vip_inscription.set(channel.id)
        await ctx.send("Channel configuré!")

    @tournamentset.command(name="checkin")
    async def tournamentset_checkin(
        self, ctx: commands.Context, *, channel: discord.TextChannel
    ):
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
    async def tournamentset_checkinrole(
        self, ctx: commands.Context, *, role: discord.Role
    ):
        """
        Définis le rôle de check-in.
        """
        if role.position >= ctx.guild.me.top_role.position:
            await ctx.send("Ce rôle est au dessus de mon rôle, je ne peux donc pas l'assigner.")
            return
        await self.data.guild(ctx.guild).roles.check.set(role.id)
        await ctx.send("Rôle configuré!")
    
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
                del blacklist[member.id]
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
                    text += f"- {str(member)} ({member.id})"
                else:
                    text += f"- {member_id} (le membre n'est plus sur le serveur)"
        for page in pagify(text):
            await ctx.send(page)

    @commands.command()
    @checks.mod()
    @commands.guild_only()
    async def inscription(self, ctx: commands.Context, limit: int):
        """
        Lance l'inscription pour le tournoi avec la limite de participants donnée.
        """

        async def update(message: discord.Message, embed: discord.Embed):
            while True:
                # https://github.com/Cog-Creators/Red-DiscordBot/blob/V3/develop/redbot/cogs/audio/audio.py#L3920
                sections = 20
                progress = round((current / limit) * sections)
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
                percent = round(current / limit * 100, 2)
                embed.set_field_at(
                    0,
                    name="Progression",
                    value=f"`[{text}]`\n{current}/{limit} ({percent}%) participants inscrits",
                    inline=False,
                )
                await message.edit(embed=embed)
                await asyncio.sleep(0.5)

        async def cancel():
            self.bot.remove_listener(on_message)
            self.bot.remove_listener(on_reaction_add)
            update_task.cancel()
            await channel.set_permissions(
                role, send_messages=False, read_messages=True, reason="Fermeture des inscriptions"
            )
            await channel.send("Fin des inscriptions.")
            await ctx.send(
                f"Inscription terminée, {current} membres enregistrés. Envoi du fichier..."
            )
            async with ctx.typing():
                participants = await self.data.guild(guild).current()
                content = "\n".join((str(guild.get_member(x)) for x in participants))
                file = text_to_file(content, "participants.txt")
                await ctx.send(file=file)

        async def on_message(message: discord.Message):
            nonlocal current
            if message.channel.id != channel.id:
                return
            if not MESSAGE_CHECK.match(message.content):
                return
            member = message.author
            #if not NAME_CHECK.match(member.name):
            #    return
            if member.id in blacklist:
                return
            async with self.data.guild(guild).current() as participants:
                if member.id in participants:
                    return
                participants.append(member.id)
            current += 1
            if current >= limit:
                self.bot.loop.create_task(cancel())
            try:
                await message.add_reaction("✅")
            except Exception:
                pass

        async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
            if reaction.message.id != message.id:
                return
            if reaction.emoji != "❌":
                return
            member = guild.get_member(user)
            if not self.bot.is_admin(member):
                return
            msg = await ctx.send("Annuler ?")
            result = await self._ask_for(ctx, msg, 10)
            if result is True:
                await cancel()
            else:
                await ctx.send("L'inscription n'est pas annulée.")

        current = 0
        guild = ctx.guild
        try:
            role = await self.get_tournament_role(guild)
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
        embed = discord.Embed(title="Inscription")
        embed.description = f"L'inscription est en cours dans le channel {channel.mention}"
        embed.add_field(name="Progression", value="Démarrage dans 10 secondes.", inline=False)
        embed.set_footer(text="Cliquez sur ❌ pour annuler l'inscription.")
        embed.colour = 0x00FF33
        blacklist = await self.data.guild(guild).blacklisted()
        message = await ctx.send(embed=embed)
        await message.add_reaction("❌")
        await channel.send(
            "__Inscription pour le prochain tournoi__\n\n"
            "- Envoyez `Je participe` dans ce channel pour s'inscrire\n"
            "- Éditer le message ne marche pas\n"
            "- Si vous pensez qu'il y a eu un problème, contactez un PK Thunder\n\n"
            "Ouverture dans 10 secondes."
        )
        self.bot.add_listener(on_reaction_add)
        await asyncio.sleep(10)
        self.bot.add_listener(on_message)
        update_task = self.bot.loop.create_task(update(message, embed))
        await channel.set_permissions(
            role, send_messages=True, read_messages=True, reason="Ouverture des inscriptions"
        )

    @commands.command()
    @checks.mod()
    async def valid(self, ctx: commands.Context, number: int):
        """
        Valide un certain nombre de membres pour l'inscription.
        """

        async def update(message: discord.Message):
            await asyncio.sleep(1)
            while True:
                # https://github.com/Cog-Creators/Red-DiscordBot/blob/V3/develop/redbot/cogs/audio/audio.py#L3920
                sections = 30
                progress = round((done / number) * sections)
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
                percent = round(done / number * 100, 2)
                await message.edit(
                    content=(
                        f"Ajout des rôles. Temps estimé: {eta*10} secondes.\n"
                        f"Effectué : {done}/{number} ({percent}%)\n"
                        f"`[{text}]`"
                    )
                )
                await asyncio.sleep(5)

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
        message = await ctx.send(f"Ajouter le rôle de participant ({role.name}) à {total} membres ?")
        result = await self._ask_for(ctx, message)
        if result is False:
            await ctx.send("Annulation.")
            return
        try:
            role = await self.get_participant_role(guild)
        except UserInputError as e:
            await ctx.send(e.args[0])
            return
        done = 0
        if number < 11:
            eta = 1
        else:
            eta = (number // 10) * 10
        await ctx.send("Envoi de la liste...")
        content = [{"id": x.id, "tag": str(x)} for x in participants]
        file = text_to_file(json.dumps(content), filename="inscriptions.json")
        await ctx.send(file=file)
        message = await ctx.send(f"Ajout des rôles. Temps estimé: {eta} secondes.")
        task = self.bot.loop.create_task(update(message))
        fails = ""
        success = []
        for member in participants:
            try:
                await member.add_roles(role, reason="Participation au tournoi.")
                await member.edit(nickname=None)
            except discord.errors.HTTPException as e:
                fails += f"{member.id} ({member.name}): {type(e)} {e.args[0]}\n"
            else:
                done += 1
                success.append(member)
        files = [text_to_file("\n".join([x.name for x in success]), filename="participants.txt")]
        if fails:
            files.append(text_to_file(fails, filename="echecs.txt"))
        task.cancel()
        await ctx.send(f"Terminé! {done}/{number} membres mis à jour.", files=files)

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
        async with ctx.typing():
            async for message in channel.history(oldest_first=True, after=after):
                if not MESSAGE_CHECK.match(message.content):
                    return
                member = message.author
                if not NAME_CHECK.match(member.name):
                    return
                if member.id in blacklist:
                    return
                participants.append(member)
                if len(participants) >= limit:
                    break
        if len(participants) < limit:
            await ctx.send(f"Pas assez de participants trouvés ({len(participants)}/{limit})")
            return
        await self.data.guild(guild).current.set(participants)
        await ctx.send(f"Inscription terminée, {len(participants)} membres enregistrés. Envoi du fichier...")
        async with ctx.typing():
            content = "\n".join((str(guild.get_member(x)) for x in participants))
            file = text_to_file(content, "participants.txt")
            await ctx.send(file=file)

    @commands.command()
    async def namecheck(self, ctx: commands.Context, *, text: str = None):
        """
        Vérifie si votre pseudo respecte le règlement.
        """
        name = "texte" if text else "pseudo"
        if NAME_CHECK.match(text or ctx.author.name):
            await ctx.send(f"Votre {name} est valide !")
        else:
            await ctx.send(f"Votre {name} n'est pas valide.")
    
    @commands.command()
    @checks.mod()
    async def startcheck(self, ctx: commands.Context):
        """
        Démarre la phase de check pendant 30 minutes.
        """

        async def update(message: discord.Message, embed: discord.Embed):
            nonlocal start_time
            while True:
                current = len(checked)
                # https://github.com/Cog-Creators/Red-DiscordBot/blob/V3/develop/redbot/cogs/audio/audio.py#L3920
                sections = 20
                progress = round((current / total) * sections)
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
                percent = round(current / total * 100, 2)
                embed.set_field_at(
                    0,
                    name="Progression",
                    value=f"`[{text}]`\n{current}/{total} ({percent}%) participants check",
                    inline=False,
                )
                embed.set_field_at(
                    1,
                    name="Temps restant",
                    value=str(start_time),
                    inline=True,
                )
                if fails:
                    if list(embed.fields) > 2:
                        embed.set_field_at(2, name="Erreurs", value=f"{len(fails)} échecs.", inline=True)
                    else:
                        embed.add_field(name="Erreurs", value=f"{len(fails)} échecs.", inline=True)
                await message.edit(embed=embed)
                await asyncio.sleep(2)
                start_time -= timedelta(seconds=2)
            await cancel()

        async def cancel():
            self.bot.remove_listener(on_message)
            self.bot.remove_listener(on_reaction_add)
            update_task.cancel()
            await channel.set_permissions(
                role, send_messages=False, reason="Fermeture du check-in"
            )
            await channel.send("Fin du check-in.")
            await ctx.send(
                f"Check-in terminé, {len(checked)} membres enregistrés. Envoi du fichier..."
            )
            async with ctx.typing():
                content = "\n".join((str(x) for x in checked))
                files = [text_to_file(content, "participants.txt")]
                if fails:
                    content = "\n".join(f"{str(x)}: {type(e)}: {e.args[0]}" for x, e in fails)
                    files.append(text_to_file(content, "fails.txt"))
                await ctx.send(files=files)

        async def on_message(message: discord.Message):
            nonlocal checked
            if message.channel.id != channel.id:
                return
            if not MESSAGE_CHECK.match(message.content):
                return
            member = message.author
            try:
                await member.add_roles(check_role, reason="Check-in tournoi")
            except discord.errors.HTTPException as e:
                fails.append((member, e))
                return
            checked.append(member)
            if len(checked) >= total:
                self.bot.loop.create_task(cancel())
            try:
                await message.add_reaction("✅")
            except Exception:
                pass

        async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
            if reaction.message.id != message.id:
                return
            if reaction.emoji != "❌":
                return
            member = guild.get_member(user)
            if not self.bot.is_admin(member):
                return
            msg = await ctx.send("Annuler ?")
            result = await self._ask_for(ctx, msg, 10)
            if result is True:
                await cancel()
            else:
                await ctx.send("L'inscription n'est pas annulée.")
        
        guild = ctx.guild
        try:
            role = await self.get_tournament_role(guild)
            check_role = await self.get_checkin_role(guild)
            participant_role = await self.get_participant_role(guild)
            channel = await self.get_checkin_channel(guild)
        except UserInputError as e:
            await ctx.send(e.args[0])
            return
        total = len(participant_role.members)
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
        embed = discord.Embed(title="Check-in")
        embed.description = f"Le check-in est en cours dans le channel {channel.mention}"
        embed.add_field(name="Progression", value="Démarrage dans 10 secondes.", inline=False)
        embed.add_field(name="Temps restant", value="0:30:00", inline=False)
        embed.set_footer(text="Cliquez sur ❌ pour annuler l'inscription.")
        embed.colour = 0x0033FF
        message = await ctx.send(embed=embed)
        await message.add_reaction("❌")
        await channel.send(
            "__Check pour le prochain tournoi__\n\n"
            "- Envoyez `check` dans ce channel pour confirmer l'inscription\n"
            "- Éditer le message ne marche pas\n"
            "- Si vous pensez qu'il y a eu un problème, contactez un PK Thunder\n\n"
            "Ouverture dans 10 secondes."
        )
        checked = []
        fails = []
        start_time = timedelta(seconds=1800)
        self.bot.add_listener(on_reaction_add)
        await asyncio.sleep(10)
        self.bot.add_listener(on_message)
        update_task = self.bot.loop.create_task(update(message, embed))
        await channel.set_permissions(
            role, send_messages=True, reason="Ouverture du check-in"
        )
