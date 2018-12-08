import datetime
import sys

import discord
import asyncio

from bot.utils.tools import separator

from bot.db.models import RaidsState
from bot.db.db import Session


def raids_embed(setting):
    clan_banner_url = f"http://services.runescape.com/m=avatar-rs/l=3/a=869/{setting.clan_name}/clanmotif.png"
    raids_notif_embed = discord.Embed(title="**Raids**", color=discord.Colour.dark_blue())
    raids_notif_embed.set_thumbnail(url=clan_banner_url)

    raids_notif_embed.add_field(
        name="Marque presença para os Raids de 21:00",
        value=f"<#{setting.chat.get('raids_chat')}>\n"
        f"\n"
        f"É obrigatório ter a tag <@&376410304277512192>\n    - Leia os tópicos fixos para saber como obter\n"
        f"\n"
        f"Não mande mensagens desnecessárias no <#{setting.chat.get('raids_chat')}>\n"
        f"\n"
        f"Não marque presença mais de uma vez\n"
        f"\n"
        f"Esteja online no jogo no mundo 75 até 20:50 em ponto.\n"
        f"- Risco de remoção do time caso contrário. Não cause atrasos",
        inline=False
    )
    return raids_notif_embed


async def raids_notification(setting, user, channel, start_day, channel_public=None, time_to_send="23:00:00"):
    print("Starting Raids notifications task.")
    while True:
        today = datetime.datetime.utcnow().date()
        check_day = (today - start_day).days % 2
        if check_day == 0 or "testraid" in sys.argv:
            date = str(datetime.datetime.utcnow().time())
            time = date[0:7]
            time_to_send = time_to_send[0:7]
            if time == time_to_send or "testraid" in sys.argv:
                session = Session()
                state = session.query(RaidsState).first()
                if not state:
                    state = RaidsState(notifications=True)
                    session.add(state)
                    session.commit()
                current_state = state.notifications
                session.close()
                if not current_state:
                    print("Notificação de Raids não foi enviada. Desabilitado.")
                    await asyncio.sleep(60)
                    continue
                team_list = []
                embed = raids_embed(setting=setting)
                print(f"$ Sent Raids notification at: {time}")
                await channel.send(content=f"<@&{setting.role.get('raids')}>", embed=embed, delete_after=60 * 90)
                team_embed = discord.Embed(title=f"__Time Raids__ - {len(team_list)}/10")
                raids_team_message = await channel.send(embed=team_embed, delete_after=60 * 90)
                invite_embed = discord.Embed(
                    title=f"Marque presença para 'Raids' (10 pessoas)",
                    description=f"{separator}\nTime: {channel.mention}\nRequisito: <@&{setting.role.get('raids')}>\n\n"
                    f"Marque presença apenas se for estar **online** no jogo até 20:50 em ponto "
                    f"**no Mundo 75.**\n\n"
                    f"`in`: Marcar presença\n"
                    f"`out`: Retirar presença"
                )
                await channel_public.send(embed=invite_embed)
                last_message = await channel_public.history().get(author=user)
                sent_time = datetime.datetime.now()
                while True:
                    async for message in channel_public.history(after=last_message):
                        if message.content.lower() == 'in':
                            await message.delete()
                            if len(team_list) >= 10:
                                await channel_public.send(
                                    f"{message.author.mention}, o time de Raids já está cheio! ({len(team_list)}/10)"
                                    f"\n(*`in`*)")
                            else:
                                if 'Raids' in str(message.author.roles):
                                    if message.author.mention in team_list:
                                        await channel_public.send(
                                            f"Ei {message.author.mention}, você já está no time! Não tente me enganar."
                                            f"\n(*`in`*)")
                                    else:
                                        team_list.append(message.author.mention)
                                        await channel_public.send(
                                            f"{message.author.mention} foi adicionado ao time de Raids. "
                                            f"({len(team_list)}/10)\n(*`in`*)")
                                else:
                                    await channel_public.send(
                                        f"{message.author.mention}, você não tem permissão para ir Raids ainda. "
                                        f"Aplique agora usando o comando `{setting.prefix}raids`!\n(*`in`*)")
                        if message.content.lower() == 'out':
                            await message.delete()
                            if message.author.mention in team_list:
                                team_list.remove(message.author.mention)
                                await channel_public.send(
                                    f"{message.author.mention} foi removido do time de Raids. ({len(team_list)}/10)"
                                    f"\n(*`out`*)")
                            else:
                                await channel_public.send(
                                    f"Ei {message.author.mention}, você já não estava no time! Não tente me enganar."
                                    f"\n(*`out`*)")
                        last_message = message
                    team_embed = discord.Embed(title=f"__Time Raids__ - {len(team_list)}/10")
                    for index, person in enumerate(team_list):
                        team_embed.add_field(name=separator, value=f"{index + 1}- {person}", inline=False)
                    try:
                        await raids_team_message.edit(embed=team_embed)
                    except discord.errors.NotFound:
                        break
                    diff = datetime.datetime.now() - sent_time
                    if diff.total_seconds() > (60 * 60):
                        print('$ No longer accepting Raids Team entries')
                        break
        await asyncio.sleep(1)
