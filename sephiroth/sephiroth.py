import discord
import aiohttp
import asyncio
import unidecode
import re
import io
import logging
import imageio

from moviepy import editor

from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path

log = logging.getLogger("red.laggron.sephiroth")


AUDIO_FILE_NAME = "one winged angel.wav"
OUTPUT_FILE_EXT = ".mp4"

IMAGE_LINKS = re.compile(
    r"(https?:\/\/[^\"\'\s]*\.(?:png|jpg|jpeg|gif|png|svg)(\?size=[0-9]*)?)", flags=re.I
)
EMOJI_REGEX = re.compile(r"(<(a)?:[a-zA-Z0-9\_]+:([0-9]+)>)")
MENTION_REGEX = re.compile(r"<@!?([0-9]+)>")
ID_REGEX = re.compile(r"[0-9]{17,}")


class ImageFinder(commands.Converter):
    """
    This is a class to convert notsobots image searching capabilities
    into a more general converter class

    TrustyJAID is the author of this class and was made for the notsobot cog
    https://github.com/TrustyJAID/Trusty-cogs/blob/master/notsobot/converter.py
    """

    async def convert(self, ctx, argument):
        attachments = ctx.message.attachments
        mentions = MENTION_REGEX.finditer(argument)
        matches = IMAGE_LINKS.finditer(argument)
        emojis = EMOJI_REGEX.finditer(argument)
        ids = ID_REGEX.finditer(argument)
        urls = []
        if matches:
            for match in matches:
                urls.append(match.group(1))
        if emojis:
            for emoji in emojis:
                ext = "gif" if emoji.group(2) else "png"
                url = "https://cdn.discordapp.com/emojis/{id}.{ext}?v=1".format(
                    id=emoji.group(3), ext=ext
                )
                urls.append(url)
        if mentions:
            for mention in mentions:
                user = ctx.guild.get_member(int(mention.group(1)))
                if user.is_avatar_animated():
                    url = IMAGE_LINKS.search(str(user.avatar_url_as(format="gif")))
                    urls.append(url.group(1))
                else:
                    url = IMAGE_LINKS.search(str(user.avatar_url_as(format="png")))
                    urls.append(url.group(1))
        if not urls and ids:
            for possible_id in ids:
                user = ctx.guild.get_member(int(possible_id.group(0)))
                if user:
                    if user.is_avatar_animated():
                        url = IMAGE_LINKS.search(str(user.avatar_url_as(format="gif")))
                        urls.append(url.group(1))
                    else:
                        url = IMAGE_LINKS.search(str(user.avatar_url_as(format="png")))
                        urls.append(url.group(1))
        if attachments:
            for attachment in attachments:
                urls.append(attachment.url)
        if not urls:
            for m in ctx.guild.members:
                if argument.lower() in unidecode.unidecode(m.display_name.lower()):
                    # display_name so we can get the nick of the user first
                    # without being NoneType and then check username if that matches
                    # what we're expecting
                    urls.append(str(m.avatar_url_as(format="png")))
                    continue
                if argument.lower() in unidecode.unidecode(m.name.lower()):
                    urls.append(str(m.avatar_url_as(format="png")))
                    continue

        if not urls:
            raise commands.BadArgument("No images provided.")
        return urls

    async def search_for_images(self, ctx):
        urls = []
        async for message in ctx.channel.history(limit=10):
            if message.attachments:
                for attachment in message.attachments:
                    urls.append(attachment.url)
            match = IMAGE_LINKS.match(message.content)
            if match:
                urls.append(match.group(1))
        if not urls:
            raise commands.BadArgument("No Images found in recent history.")
        return urls


class Sephiroth(commands.Cog):
    """
    Oh god oh fuck
    """

    def __init__(self, bot: Red):
        self.bot = bot
        (cog_data_path(self) / "output").mkdir(exist_ok=True)

    async def bytes_download(self, url: str):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        mime = resp.headers.get("Content-type", "").lower()
                        b = io.BytesIO(data)
                        b.seek(0)
                        return b, mime
                    else:
                        return False, False
        except asyncio.TimeoutError:
            return False, False
        except Exception:
            log.error("Error downloading to bytes", exc_info=True)
            return False, False

    def make_video(self, image: io.BytesIO, user_id: int):
        base_path = cog_data_path(self)
        image_clip: editor.ImageClip = editor.ImageClip(imageio.imread(image))
        audio_clip = editor.AudioFileClip(str(base_path / AUDIO_FILE_NAME))
        image_clip = image_clip.set_duration(audio_clip.duration).set_audio(audio_clip)
        image_clip.write_videofile(f"{base_path / 'output'}/{user_id}{OUTPUT_FILE_EXT}", fps=30)

    @commands.command(name="owa")
    @commands.cooldown(1, 15, commands.BucketType.user)
    @commands.cooldown(10, 60, commands.BucketType.guild)
    async def one_winged_angel(self, ctx: commands.Context, *, images: ImageFinder = None):
        """
        Génère une vidéo d'une image avec One Winged Angel par dessus.
        """
        if images is None:
            images = await ImageFinder().search_for_images(ctx)
        async with ctx.typing():
            b, mime = await self.bytes_download(images[0])
            if b is False:
                await ctx.send(":warning: Le téléchargement a échoué.")
                return
            task = self.bot.loop.run_in_executor(None, self.make_video, b, ctx.author.id)
            try:
                await asyncio.wait_for(task, timeout=60)
            except (asyncio.TimeoutError, TypeError, ValueError):
                return await ctx.send(
                    "Cette image est trop large ou bien le format n'est pas supporté."
                )
            await ctx.send(
                file=discord.File(
                    f"{cog_data_path(self) / 'output'}/{ctx.author.id}{OUTPUT_FILE_EXT}"
                )
            )
            (cog_data_path(self) / "output" / f"{ctx.author.id}{OUTPUT_FILE_EXT}").unlink()
