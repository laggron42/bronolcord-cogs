from .tournamentmanager import TournamentManager


def setup(bot):
    n = TournamentManager(bot)
    bot.add_cog(n)
