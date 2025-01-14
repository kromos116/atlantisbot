import asyncio
import os
import io
import sys
import datetime
import inspect
import sqlite3
import traceback
import textwrap
from contextlib import redirect_stdout

from discord.ext import commands
import discord

from bot.bot_client import Bot
from bot.orm.models import RaidsState, Team, PlayerActivities, AdvLogState, AmigoSecretoState, AmigoSecretoPerson, \
    DisabledCommand
from bot.utils.tools import separator, plot_table


class Owner(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        self._last_result = None
        self.sessions = set()

    @commands.is_owner()
    @commands.command()
    async def restart(self, ctx: commands.Context):
        await ctx.send("Restarting bot...")
        if self.bot.setting.mode == 'dev':
            # Heroku will make sure the bot logs back in after getting logged out without needing to do it manually
            os.execv(sys.executable, ['python3'] + sys.argv)
        await self.bot.logout()

    @commands.is_owner()
    @commands.command(aliases=['reload'])
    async def reload_cog(self, ctx: commands.Context, cog: str):
        """Reloads a cog"""
        try:
            self.bot.reload_extension(f'bot.cogs.{cog}')
            return await ctx.send(f'Extensão {cog} reiniciada com sucesso.')
        except ModuleNotFoundError:
            return await ctx.send(f"Extensão {cog} não existe.")
        except Exception as e:
            return await ctx.send(f'Erro ao reiniciar extensão {cog}:\n {type(e).__name__} : {e}')

    @commands.is_owner()
    @commands.command(aliases=['reloadall'])
    async def reload_all_cogs(self, ctx: commands.Context):
        """Reloads all cogs"""
        err = await self.bot.reload_all_extensions()
        if err:
            return await ctx.send('Houve algum erro reiniciando extensões. Verificar os Logs do bot.')
        return await ctx.send('Todas as extensões foram reiniciadas com sucesso.')

    @commands.is_owner()
    @commands.command()
    async def disable(self, ctx: commands.Context, command_name: str):
        """Disables a command"""
        command = self.bot.get_command(command_name)
        if not command:
            return await ctx.send(f"O comando {command_name} não existe.")
        if not command.enabled:
            return await ctx.send(f"O comando {command_name} já está desabilitado.")
        with self.bot.db_session() as session:
            command.enabled = False
            session.add(DisabledCommand(name=command_name))
        return await ctx.send(f"Comando {command_name} desabilitado com sucesso.")

    @commands.is_owner()
    @commands.command()
    async def enable(self, ctx: commands.Context, command_name: str):
        """Enables a command"""
        command = self.bot.get_command(command_name)
        if not command:
            return await ctx.send(f"O comando {command_name} não existe.")
        if command.enabled:
            return await ctx.send(f"O comando {command_name} já está habilitado.")

        with self.bot.db_session() as session:
            disabled_command = session.query(DisabledCommand).filter_by(name=command_name).first()
            session.delete(disabled_command)
            command.enabled = True
        return await ctx.send(f"Comando {command_name} habilitado com sucesso.")

    @staticmethod
    def cleanup_code(content):
        """Automatically removes code blocks from the code."""
        # remove ```py\n```
        if content.startswith('```') and content.endswith('```'):
            return '\n'.join(content.split('\n')[1:-1])

        # remove `foo`
        return content.strip('` \n')

    @commands.is_owner()
    @commands.command(pass_context=True, hidden=True, name='eval')
    async def _eval(self, ctx, *, body: str):
        """
        https://github.com/Rapptz/RoboDanny/blob/rewrite/cogs/admin.py#L216-L261
        Evaluates code in a message
        """

        env = {
            'bot': self.bot,
            'ctx': ctx,
            'channel': ctx.channel,
            'author': ctx.author,
            'guild': ctx.guild,
            'message': ctx.message,
            '_': self._last_result
        }

        env.update(globals())

        body = self.cleanup_code(body)
        stdout = io.StringIO()

        to_compile = f'async def func():\n{textwrap.indent(body, "  ")}'

        try:
            exec(to_compile, env)
        except Exception as e:
            return await ctx.send(f'```py\n{e.__class__.__name__}: {e}\n```')

        func = env['func']
        try:
            with redirect_stdout(stdout):
                ret = await func()
        except Exception:
            value = stdout.getvalue()
            await ctx.send(f'```py\n{value}{traceback.format_exc()}\n```')
        else:
            value = stdout.getvalue()
            try:
                await ctx.message.add_reaction('\u2705')
            except Exception:
                pass

            if ret is None:
                if value:
                    await ctx.send(f'```py\n{value}\n```')
            else:
                self._last_result = ret
                await ctx.send(f'```py\n{value}{ret}\n```')

    @staticmethod
    def get_syntax_error(e):
        if e.text is None:
            return f'```py\n{e.__class__.__name__}: {e}\n```'
        return f'```py\n{e.text}{"^":>{e.offset}}\n{e.__class__.__name__}: {e}```'

    @commands.is_owner()
    @commands.command(pass_context=True, hidden=True)
    async def repl(self, ctx):
        """Launches an interactive REPL session."""
        variables = {
            'ctx': ctx,
            'bot': self.bot,
            'message': ctx.message,
            'guild': ctx.guild,
            'channel': ctx.channel,
            'author': ctx.author,
            '_': None,
        }

        if ctx.channel.id in self.sessions:
            await ctx.send('Already running a REPL session in this channel. Exit it with `quit`.')
            return

        self.sessions.add(ctx.channel.id)
        await ctx.send('Enter code to execute or evaluate. `exit()` or `quit` to exit.')

        def check(m):
            return m.author.id == ctx.author.id and \
                   m.channel.id == ctx.channel.id and \
                   m.content.startswith('`')

        while True:
            try:
                response = await self.bot.wait_for('message', check=check, timeout=10.0 * 60.0)
            except asyncio.TimeoutError:
                await ctx.send('Exiting REPL session.')
                self.sessions.remove(ctx.channel.id)
                break

            cleaned = self.cleanup_code(response.content)

            if cleaned in ('quit', 'exit', 'exit()'):
                await ctx.send('Exiting.')
                self.sessions.remove(ctx.channel.id)
                return
            code = None
            executor = exec
            if cleaned.count('\n') == 0:
                # single statement, potentially 'eval'
                try:
                    code = compile(cleaned, '<repl session>', 'eval')
                except SyntaxError:
                    pass
                else:
                    executor = eval

            if executor is exec:
                try:
                    code = compile(cleaned, '<repl session>', 'exec')
                except SyntaxError as e:
                    await ctx.send(self.get_syntax_error(e))
                    continue

            variables['message'] = response

            fmt = None
            stdout = io.StringIO()

            try:
                with redirect_stdout(stdout):
                    result = executor(code, variables)
                    if inspect.isawaitable(result):
                        result = await result
            except Exception:
                value = stdout.getvalue()
                fmt = f'```py\n{value}{traceback.format_exc()}\n```'
            else:
                value = stdout.getvalue()
                if result is not None:
                    fmt = f'```py\n{value}{result}\n```'
                    variables['_'] = result
                elif value:
                    fmt = f'```py\n{value}\n```'

            try:
                if fmt is not None:
                    if len(fmt) > 2000:
                        await ctx.send('Content too big to be printed.')
                    else:
                        await ctx.send(fmt)
            except discord.Forbidden:
                pass
            except discord.HTTPException as e:
                await ctx.send(f'Unexpected error: `{e}`')

    @commands.is_owner()
    @commands.command(aliases=['sendtable'])
    async def send_table(self, ctx: commands.Context, table: str, safe: bool = True):
        if not safe:
            await ctx.send(f"Você tem certeza que deseja enviar uma imagem da tabela '{table}'? (y/N)")

            def check(msg):
                return ctx.author == msg.author

            try:
                message = await self.bot.wait_for('message', check=check, timeout=60.0)
            except asyncio.TimeoutError:
                return await ctx.send("Comando cancelado. Tempo expirado.")
            if 'y' not in message.content.lower():
                return await ctx.send("Envio de Tabela cancelada.")
        try:
            plot_table(table, table, safe=safe)
        except IndexError:
            return await ctx.send("Não há nenhuma linha nessa tabela.")
        except sqlite3.OperationalError:
            return await ctx.send("Essa tabela não existe.")
        return await ctx.send(file=discord.File(f'{table}.tmp.png'))

    @commands.command(aliases=['admin'])
    async def admin_commands(self, ctx: commands.Context):
        clan_banner = f"http://services.runescape.com/m=avatar-rs/l=3/a=869/{self.bot.setting.clan_name}/clanmotif.png"

        embed = discord.Embed(title="__Comandos Admin__", description="", color=discord.Color.blue())
        prefix = self.bot.setting.prefix

        embed.add_field(
            name=f"{prefix}timesativos",
            value="Ver informações sobre os times ativos no momento",
            inline=False
        )
        embed.add_field(
            name=f"{prefix}check_raids",
            value="Verificar se notificações de Raids estão habilitadas ou não",
            inline=False
        )
        embed.add_field(
            name=f"{prefix}toggle_raids",
            value="Desativar/Ativar notificações de Raids",
            inline=False
        )
        embed.add_field(
            name=f"{prefix}check_advlog",
            value="Verificar se mensagens do Adv. Log estão habilitadas ou não",
            inline=False
        )
        embed.add_field(
            name=f"{prefix}toggle_advlog",
            value="Desativar/Ativar mensagens do Adv. Log",
            inline=False
        )
        embed.set_author(
            icon_url=clan_banner,
            name="AtlantisBot"
        )
        embed.set_thumbnail(
            url=self.bot.setting.banner_image
        )
        await ctx.send(embed=embed)

    @commands.command(aliases=['timesativos', 'times_ativos'])
    async def running_teams(self, ctx: commands.Context):
        running_teams_embed = discord.Embed(title='__Times Ativos__', description="", color=discord.Color.red())
        with self.bot.db_session() as session:
            teams = session.query(Team).all()
            if not teams:
                running_teams_embed.add_field(name=separator, value=f"Nenhum time ativo no momento.")
            for team in teams:
                running_teams_embed.add_field(
                    name=separator,
                    value=f"**Título:** {team.title}\n"
                    f"**PK:** {team.id}\n"
                    f"**Team ID:** {team.team_id}\n"
                    f"**Chat:** <#{team.team_channel_id}>\n"
                    f"**Criado por:** <@{team.author_id}>\n"
                    f"**Criado em:** {team.created_date}"
                )
        await ctx.send(embed=running_teams_embed)

    @commands.command()
    async def check_raids(self, ctx: commands.Context):
        notifications = self.raids_notifications()
        return await ctx.send(f"Notificações de Raids estão {'habilitadas' if notifications else 'desabilitadas'}.")

    @commands.command()
    async def toggle_raids(self, ctx: commands.Context):
        toggle = self.toggle_raids_notifications()
        return await ctx.send(f"Notificações de Raids agora estão {'habilitadas' if toggle else 'desabilitadas'}.")

    @commands.command()
    async def check_advlog(self, ctx: commands.Context):
        messages = self.advlog_messages()
        return await ctx.send(f"Mensagens do Adv log estão {'habilitadas' if messages else 'desabilitadas'}.")

    @commands.command()
    async def toggle_advlog(self, ctx: commands.Context):
        toggle = self.toggle_advlog_messages()
        return await ctx.send(f"Mensagens do Adv log agora estão {'habilitadas' if toggle else 'desabilitadas'}.")

    @commands.command()
    async def status(self, ctx: commands.Context):
        with self.bot.db_session() as session:
            team_count = session.query(Team).count()
            advlog_count = session.query(PlayerActivities).count()
            amigosecreto_count = session.query(AmigoSecretoPerson).count()
            raids_notif = f"{'Habilitadas' if self.raids_notifications() else 'Desabilitadas'}"
            advlog = f"{'Habilitadas' if self.advlog_messages() else 'Desabilitadas'}"
            amigo_secreto = f"{'Ativo' if self.secret_santa() else 'Inativo'}"
        embed = discord.Embed(title="", description="", color=discord.Color.blue())

        embed.set_footer(text=f"Uptime: {datetime.datetime.utcnow() - self.bot.start_time}")

        embed.set_thumbnail(url=self.bot.setting.banner_image)

        embed.add_field(name="Times ativos", value=team_count)
        embed.add_field(name="Adv Log Entries", value=advlog_count)
        embed.add_field(name="Notificações de Raids", value=raids_notif)
        embed.add_field(name="Mensagens de Adv Log", value=advlog)
        embed.add_field(name="Amigo Secreto", value=amigo_secreto)
        embed.add_field(name="Amigo Secreto Entries", value=amigosecreto_count)
        return await ctx.send(embed=embed)

    def secret_santa(self):
        with self.bot.db_session() as session:
            state = session.query(AmigoSecretoState).first()
            if not state:
                state = AmigoSecretoState(activated=False)
                session.add(state)
                session.commit()
            state_ = state.activated
        return state_

    def raids_notifications(self):
        with self.bot.db_session() as session:
            state = session.query(RaidsState).first()
            if not state:
                state = RaidsState(notifications=True)
                session.add(state)
                session.commit()
            state_ = state.notifications
        return state_

    def toggle_raids_notifications(self):
        with self.bot.db_session() as session:
            state = session.query(RaidsState).first()
            if not state:
                state = RaidsState(notifications=True)
                session.add(state)
                session.commit()
            state.notifications = not state.notifications
            state_ = state.notifications
            session.commit()
        return state_

    def advlog_messages(self):
        with self.bot.db_session() as session:
            state = session.query(AdvLogState).first()
            if not state:
                state = AdvLogState(messages=True)
                session.add(state)
                session.commit()
            state_ = state.messages
        return state_

    def toggle_advlog_messages(self):
        with self.bot.db_session() as session:
            state = session.query(AdvLogState).first()
            if not state:
                state = AdvLogState(messages=True)
                session.add(state)
                session.commit()
            state.messages = not state.messages
            state_ = state.messages
            session.commit()
        return state_


def setup(bot):
    bot.add_cog(Owner(bot))
