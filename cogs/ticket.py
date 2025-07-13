import discord
from discord.ext import commands
from discord import app_commands, File
from discord.ui import View, Button
from datetime import datetime, timezone
from io import BytesIO
import base64
import html
import logging

from utils import config

logger = logging.getLogger("bot")

class HelpView(View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="문의하기", style=discord.ButtonStyle.primary, custom_id="open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        member = interaction.user
        cat = guild.get_channel(config.TICKET_CATEGORY_ID)

        if cat is None:
            await interaction.response.send_message("❌ 티켓 카테고리를 찾을 수 없습니다.", ephemeral=True)
            logger.error(f"❌ [ticket] 카테고리 ID `{config.TICKET_CATEGORY_ID}`를 찾을 수 없습니다.")
            return

        staff_role = guild.get_role(1389711188962574437)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
            staff_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)
        }

        existing = discord.utils.get(cat.text_channels, name=f"ticket-{member.id}")
        if existing:
            await interaction.response.send_message(
                f"❗ 이미 열린 티켓이 있습니다: {existing.mention}", ephemeral=True
            )
            return

        ticket_chan = await cat.create_text_channel(f"ticket-{member.id}", overwrites=overwrites)
        await interaction.response.send_message(
            f"✅ 티켓 채널이 생성되었습니다: {ticket_chan.mention}", ephemeral=True
        )

        embed = discord.Embed(
            title="🎫 새 티켓 생성됨",
            description=f"{member.mention}님의 문의입니다.",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="생성자", value=f"{member} | `{member.id}`", inline=False)
        embed.add_field(name="티켓 채널", value=ticket_chan.mention, inline=False)

        try:
            await ticket_chan.send(embed=embed, view=CloseTicketView(self.bot))
        except Exception as e:
            await interaction.followup.send("⚠️ 티켓 채널에 메시지를 보내는 데 실패했습니다.", ephemeral=True)
            logger.error(f"티켓 채널에 메시지 전송 실패: {e}")

        logger.info(f"🎫 {member.display_name}님이 `{ticket_chan.name}` 티켓을 생성했습니다.")


class CloseTicketView(View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="티켓 닫기", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        try:
            channel = interaction.channel
            # Parse owner ID from channel name safely
            try:
                owner_id = int(channel.name.split("-", 1)[1])
            except (IndexError, ValueError):
                await interaction.response.send_message("❌ 이 채널은 티켓 채널이 아닙니다.", ephemeral=True)
                return

            ticket_owner = channel.guild.get_member(owner_id)
            is_owner = interaction.user.id == owner_id
            has_sup = any(r.id == 1389711188962574437 for r in interaction.user.roles)
            is_admin = interaction.user.guild_permissions.administrator

            if not (is_owner or has_sup or is_admin):
                await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
                return

            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send("⏳ 티켓을 닫는 중입니다...", ephemeral=True)

            created_ts = channel.created_at.strftime("%Y-%m-%d %H:%M UTC")

            all_msgs = [m async for m in channel.history(limit=100, oldest_first=True)]
            msgs = all_msgs[1:] if all_msgs and all_msgs[0].author.bot else all_msgs

            css = """
            @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');

            body {
              margin: 0;
              padding: 30px 15px;
              background: #f9fafb;
              color: #2e2e2e;
              font-family: 'Roboto', sans-serif;
            }

            .container {
              max-width: 900px;
              margin: 0 auto;
              background: #ffffff;
              border-radius: 16px;
              box-shadow: 0 12px 24px rgba(0,0,0,0.1);
              padding: 40px 30px;
            }

            .header {
              text-align: center;
              margin-bottom: 40px;
            }

            .header h1 {
              margin: 0;
              color: #3b82f6;
              font-size: 2.75rem;
              font-weight: 700;
              letter-spacing: -0.02em;
            }

            .header .meta {
              font-size: 1rem;
              color: #6b7280;
              margin-top: 10px;
              font-weight: 400;
            }

            .messages {
              display: flex;
              flex-direction: column;
              gap: 28px;
            }

            .msg {
              display: flex;
              gap: 20px;
              align-items: flex-start;
              background: #f3f4f6;
              border-radius: 14px;
              padding: 16px 20px;
              box-shadow: 0 4px 8px rgba(59,130,246,0.1);
              transition: background-color 0.2s ease;
            }

            .msg:hover {
              background-color: #e0e7ff;
            }

            .avatar {
              width: 50px;
              height: 50px;
              border-radius: 50%;
              flex-shrink: 0;
              box-shadow: 0 2px 8px rgba(59,130,246,0.2);
            }

            .bubble {
              flex: 1;
            }

            .username {
              font-weight: 700;
              font-size: 1.1rem;
              color: #1e40af;
              display: inline-block;
            }

            .timestamp {
              font-size: 0.8rem;
              color: #9ca3af;
              margin-left: 14px;
              font-weight: 500;
            }

            .text {
              margin-top: 8px;
              font-size: 1rem;
              line-height: 1.55;
              white-space: pre-wrap;
              color: #374151;
            }

            img.attachment {
              max-width: 100%;
              border-radius: 14px;
              margin-top: 16px;
              box-shadow: 0 8px 20px rgba(59,130,246,0.1);
              border: 1px solid #d1d5db;
            }

            .footer {
              text-align: center;
              margin-top: 50px;
              font-size: 0.9rem;
              color: #6b7280;
              font-weight: 400;
            }
            """

            messages_html = ""
            for m in msgs:
                when = m.created_at.strftime("%Y-%m-%d %H:%M")
                name = html.escape(m.author.display_name)
                content = html.escape(m.content or "")
                avatar = m.author.avatar.url if m.author.avatar else ""

                messages_html += f"""
    <div class="msg">
      <img class="avatar" src="{avatar}" alt="avatar">
      <div class="bubble">
        <span class="username">{name}</span>
        <span class="timestamp">{when}</span>
        <div class="text">{content}</div>
    """

                for att in m.attachments:
                    b64 = base64.b64encode(await att.read()).decode("ascii")
                    ctype = att.content_type or "image/png"
                    messages_html += f"""
        <img class="attachment" src="data:{ctype};base64,{b64}" alt="{att.filename}">
    """

                messages_html += "  </div>\n</div>"

            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            html_doc = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8">
      <style>{css}</style>
    </head>
    <body>
      <div class="container">
        <div class="header">
          <h1>Transcript for {channel.name}</h1>
          <p class="meta">Created: {created_ts} • Owner: {ticket_owner}</p>
        </div>
        <div class="messages">
          {messages_html}
        </div>
        <div class="footer">Generated by {self.bot.user.name} on {now_utc}</div>
      </div>
    </body>
    </html>
    """.strip()

            buf = BytesIO(html_doc.encode("utf-8"))
            buf.seek(0)

            close_embed = discord.Embed(
                title="🎫 티켓 닫힘",
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc)
            )
            close_embed.add_field(name="티켓", value=channel.name, inline=False)
            close_embed.add_field(name="생성자", value=str(ticket_owner), inline=False)
            close_embed.add_field(name="닫은 사람", value=str(interaction.user), inline=False)

            history_ch = channel.guild.get_channel(config.HISTORY_CHANNEL_ID)
            if history_ch:
                await history_ch.send(embed=close_embed, file=File(buf, filename=f"{channel.name}.html"))
                logger.info(f"✅ {ticket_owner.display_name}님의 `{channel.name}` 티켓이 닫히고 기록이 저장되었습니다.")
            else:
                logger.warning("⚠️ HISTORY 채널을 찾을 수 없습니다.")

            await channel.delete(reason="티켓 종료")

        except Exception as e:
            logger.error(f"티켓 종료 중 오류 발생: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ 티켓 닫기 중 오류가 발생했습니다.", ephemeral=True)


class TicketSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def send_ticket_request_message(self):
        channel = self.bot.get_channel(1389742771253805077)
        if channel is None:
            logger.error("티켓 요청 채널을 찾을 수 없습니다!")
            return

        try:
            await channel.purge(limit=None)
        except Exception as e:
            logger.error(f"{channel.name} 채널의 메시지 삭제 실패: {e}")

        embed = discord.Embed(
            title="✨ 티켓 생성하기 ✨",
            description=(
                "서버 이용 중 불편하시거나 개선 제안이 있으신가요?\n"
                "아래 버튼을 눌러 문의 티켓을 열어주세요.\n"
                "운영진이 빠르게 확인하고 도움을 드리겠습니다."
            ),
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(
            url="https://cdn1.iconfinder.com/data/icons/unicons-line-vol-2/24/comment-question-256.png"
        )
        embed.set_footer(text="Exceed • 티켓 시스템")
        embed.set_author(
            name="Exceed 티켓 안내",
            icon_url="https://cdn-icons-png.flaticon.com/512/295/295128.png"
        )

        try:
            await channel.send(embed=embed, view=HelpView(self.bot))
            logger.info(f"{channel.name} 채널에 버튼과 함께 문의 요청 메시지를 보냈습니다.")
        except Exception as e:
            logger.error(f"문의 요청 메시지 전송에 실패했습니다: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        await self.send_ticket_request_message()

    @app_commands.command(name="help", description="운영진에게 문의할 수 있는 티켓을 엽니다.")
    async def slash_help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="문의 사항이 있으신가요?",
            description=(
                "아래 '문의하기' 버튼을 눌러주세요.\n"
                "개별 티켓 채널이 생성되어 운영진이 도움을 드립니다."
            ),
            color=discord.Color.teal()
        )
        embed.set_footer(text="Exceed • 티켓 시스템")
        await interaction.response.send_message(embed=embed, view=HelpView(self.bot), ephemeral=False)


async def setup(bot):
    await bot.add_cog(TicketSystem(bot))
