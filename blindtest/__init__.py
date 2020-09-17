from .blindtest import BlindTest


def setup(bot):
    bot.add_cog(BlindTest(bot))
