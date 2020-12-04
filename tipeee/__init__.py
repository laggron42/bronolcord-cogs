from .tipeee import Tipeee


def setup(bot):
    bot.add_cog(Tipeee(bot))
