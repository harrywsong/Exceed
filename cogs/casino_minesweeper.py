# cogs/casino_minesweeper.py
import discord
from discord.ext import commands
from discord import app_commands
import random
from typing import List, Tuple

from utils.logger import get_logger
from utils import config


class MinesweeperView(discord.ui.View):
    """Interactive Minesweeper game with dropdown selection"""

    def __init__(self, bot, user_id: int, bet: int, mines: int):
        super().__init__(timeout=300)  # 5 minutes timeout
        self.bot = bot
        self.user_id = user_id
        self.bet = bet
        self.mines_count = mines
        self.game_over = False
        self.game_won = False
        self.revealed_gems = 0
        self.current_multiplier = 1.0

        # 5x5 grid
        self.grid_size = 5
        self.total_cells = self.grid_size * self.grid_size
        self.total_gems = self.total_cells - self.mines_count

        # Initialize grid (True = mine, False = gem)
        self.grid = self.generate_minefield()
        self.revealed = [[False for _ in range(self.grid_size)] for _ in range(self.grid_size)]

        # Selection state
        self.selected_position = None

        # Create UI components
        self.create_components()

    def generate_minefield(self) -> List[List[bool]]:
        """Generate minefield with specified number of mines"""
        # Create flat list with mines
        cells = [True] * self.mines_count + [False] * (self.total_cells - self.mines_count)
        random.shuffle(cells)

        # Convert to 2D grid
        grid = []
        for i in range(self.grid_size):
            row = []
            for j in range(self.grid_size):
                row.append(cells[i * self.grid_size + j])
            grid.append(row)

        return grid

    def create_components(self):
        """Create UI components for the game"""
        # Position selection dropdown
        self.position_select = PositionSelect()
        self.add_item(self.position_select)

        # Action buttons
        reveal_btn = discord.ui.Button(
            label="🔍 선택한 위치 공개",
            style=discord.ButtonStyle.primary,
            custom_id="reveal"
        )
        reveal_btn.callback = self.reveal_callback
        self.add_item(reveal_btn)

        cash_out_btn = discord.ui.Button(
            label="💰 캐시아웃",
            style=discord.ButtonStyle.success,
            custom_id="cash_out"
        )
        cash_out_btn.callback = self.cash_out_callback
        self.add_item(cash_out_btn)

    async def reveal_callback(self, interaction: discord.Interaction):
        """Handle reveal button"""
        if interaction.user.id != self.user_id or self.game_over:
            await interaction.response.send_message("❌ 권한이 없습니다!", ephemeral=True)
            return

        if self.selected_position is None:
            await interaction.response.send_message("❌ 먼저 위치를 선택하세요!", ephemeral=True)
            return

        row, col = self.selected_position
        if self.revealed[row][col]:
            await interaction.response.send_message("❌ 이미 공개된 칸입니다!", ephemeral=True)
            return

        await interaction.response.defer()
        await self.reveal_cell(interaction, row, col)

    async def cash_out_callback(self, interaction: discord.Interaction):
        """Handle cash out button"""
        if interaction.user.id != self.user_id or self.game_over:
            await interaction.response.send_message("❌ 권한이 없습니다!", ephemeral=True)
            return

        if self.revealed_gems == 0:
            await interaction.response.send_message("❌ 최소 1개의 보석을 찾아야 캐시아웃 가능합니다!", ephemeral=True)
            return

        await interaction.response.defer()
        await self.end_game(interaction, True)

    def calculate_multiplier(self) -> float:
        """Calculate current multiplier based on revealed gems and mine count"""
        if self.revealed_gems == 0:
            return 1.0

        # Progressive multiplier based on risk
        # More mines = higher multiplier per gem found
        base_multiplier = 1.0
        multiplier_per_gem = 0.15 + (self.mines_count * 0.03)  # More mines = bigger multiplier

        return base_multiplier + (self.revealed_gems * multiplier_per_gem)

    async def reveal_cell(self, interaction: discord.Interaction, row: int, col: int):
        """Reveal a cell and handle game logic"""
        self.revealed[row][col] = True

        if self.grid[row][col]:  # Hit a mine
            await self.end_game(interaction, False)
        else:  # Found a gem
            self.revealed_gems += 1
            self.current_multiplier = self.calculate_multiplier()

            # Reset selection
            self.selected_position = None
            self.position_select.placeholder = "위치를 선택하세요 (예: A1, B3, C5)"

            # Check if won (found all gems)
            if self.revealed_gems >= self.total_gems:
                await self.end_game(interaction, True)
            else:
                embed = await self.create_game_embed()
                await interaction.edit_original_response(embed=embed, view=self)

    def format_grid(self) -> str:
        """Format the grid for display"""
        grid_lines = []
        grid_lines.append("```")
        grid_lines.append("    1  2  3  4  5")

        for i, letter in enumerate(['A', 'B', 'C', 'D', 'E']):
            line = f"{letter}:"
            for j in range(self.grid_size):
                if self.revealed[i][j]:
                    if self.grid[i][j]:  # Mine
                        line += " 💣"
                    else:  # Gem
                        line += " 💎"
                else:  # Hidden
                    line += " ⬛"
            grid_lines.append(line)

        grid_lines.append("```")

        return "\n".join(grid_lines)

    async def create_game_embed(self, game_ended: bool = False, won: bool = False) -> discord.Embed:
        """Create the game status embed"""
        if game_ended:
            if won:
                title = "💎 승리! 성공적으로 캐시아웃!"
                color = discord.Color.green()
                payout = int(self.bet * self.current_multiplier)
                description = f"🎯 **발견한 보석:** {self.revealed_gems}/{self.total_gems}개\n📈 **최종 배수:** {self.current_multiplier:.2f}x\n💰 **획득 코인:** +{payout:,}코인"
            else:
                title = "💣 폭발! 지뢰를 밟았습니다!"
                color = discord.Color.red()
                description = f"🎯 **발견한 보석:** {self.revealed_gems}/{self.total_gems}개\n📉 **배수:** {self.current_multiplier:.2f}x\n💸 **손실 코인:** -{self.bet:,}코인"
        else:
            title = "💣 지뢰찾기"
            color = discord.Color.blue()
            remaining_gems = self.total_gems - self.revealed_gems
            potential_payout = int(self.bet * self.current_multiplier)

            description = f"💣 **지뢰:** {self.mines_count}개 | 💎 **남은 보석:** {remaining_gems}개\n"
            description += f"🎯 **발견한 보석:** {self.revealed_gems}개\n"
            description += f"📈 **현재 배수:** {self.current_multiplier:.2f}x\n"
            description += f"💰 **예상 수익:** {potential_payout:,}코인"

            if self.selected_position:
                row, col = self.selected_position
                pos_name = f"{['A', 'B', 'C', 'D', 'E'][row]}{col + 1}"
                description += f"\n\n📍 **선택된 위치:** {pos_name}"

        embed = discord.Embed(title=title, description=description, color=color)

        # Add the game grid
        embed.add_field(name="🎮 게임 보드", value=self.format_grid(), inline=False)

        if not game_ended:
            embed.add_field(
                name="📋 게임 방법",
                value="1️⃣ 드롭다운에서 위치 선택 (A1~E5)\n2️⃣ '🔍 선택한 위치 공개' 클릭\n3️⃣ 💎 보석을 찾으면 계속, 💣 지뢰를 밟으면 게임 종료\n4️⃣ 💰 언제든지 캐시아웃 가능",
                inline=False
            )

            # Risk/Reward info
            next_multiplier = 1.0 + ((self.revealed_gems + 1) * (0.15 + (self.mines_count * 0.03)))
            next_payout = int(self.bet * next_multiplier)
            embed.add_field(
                name="📊 위험도 분석",
                value=f"🎯 **다음 보석 발견시:** {next_multiplier:.2f}x ({next_payout:,}코인)\n⚡ **성공 확률:** {((self.total_gems - self.revealed_gems) / (25 - len([r for row in self.revealed for r in row if r])) * 100):.1f}%",
                inline=False
            )

        return embed

    async def end_game(self, interaction: discord.Interaction, won: bool):
        """End the game and calculate payouts"""
        self.game_over = True
        self.game_won = won

        # Reveal all cells
        for i in range(self.grid_size):
            for j in range(self.grid_size):
                self.revealed[i][j] = True

        # Disable all components
        for item in self.children:
            item.disabled = True

        # Handle payout
        coins_cog = self.bot.get_cog('CoinsCog')
        if coins_cog and won:
            payout = int(self.bet * self.current_multiplier)
            await coins_cog.add_coins(
                self.user_id,
                payout,
                "minesweeper_win",
                f"지뢰찾기 승리: {self.revealed_gems}개 보석, {self.current_multiplier:.2f}x 배수"
            )

        embed = await self.create_game_embed(True, won)

        if coins_cog:
            new_balance = await coins_cog.get_user_coins(self.user_id)
            embed.add_field(name="💳 현재 잔액", value=f"{new_balance:,} 코인", inline=True)

        await interaction.edit_original_response(embed=embed, view=self)


class PositionSelect(discord.ui.Select):
    """Dropdown for selecting grid positions"""

    def __init__(self):
        options = []

        # Create options for each grid position
        for i, letter in enumerate(['A', 'B', 'C', 'D', 'E']):
            for j in range(1, 6):
                options.append(discord.SelectOption(
                    label=f"{letter}{j}",
                    description=f"{letter}행 {j}열",
                    value=f"{i},{j - 1}",
                    emoji="📍"
                ))

        super().__init__(
            placeholder="위치를 선택하세요 (예: A1, B3, C5)",
            options=options,
            custom_id="position_select"
        )

    async def callback(self, interaction: discord.Interaction):
        view: MinesweeperView = self.view
        if interaction.user.id != view.user_id or view.game_over:
            await interaction.response.send_message("❌ 권한이 없습니다!", ephemeral=True)
            return

        # Parse selected position
        row, col = map(int, self.values[0].split(','))

        if view.revealed[row][col]:
            await interaction.response.send_message("❌ 이미 공개된 칸입니다!", ephemeral=True)
            return

        # Update selection
        view.selected_position = (row, col)
        position_name = f"{['A', 'B', 'C', 'D', 'E'][row]}{col + 1}"
        self.placeholder = f"선택됨: {position_name}"

        embed = await view.create_game_embed()
        await interaction.response.edit_message(embed=embed, view=view)


class MinesweeperCog(commands.Cog):
    """Casino Minesweeper game"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("지뢰찾기", bot=bot, discord_log_channel_id=config.LOG_CHANNEL_ID)
        self.logger.info("지뢰찾기 게임 시스템이 초기화되었습니다.")

    async def validate_game(self, interaction: discord.Interaction, bet: int):
        """Validate game using casino base"""
        casino_base = self.bot.get_cog('CasinoBaseCog')
        if not casino_base:
            return False, "카지노 시스템을 찾을 수 없습니다!"

        return await casino_base.validate_game_start(
            interaction, "minesweeper", bet, 10, 200
        )

    @app_commands.command(name="지뢰찾기", description="지뢰를 피해 보석을 찾는 게임")
    @app_commands.describe(
        bet="베팅 금액 (10-200)",
        mines="지뢰 개수 (1-15, 많을수록 위험하지만 높은 수익)"
    )
    async def minesweeper(self, interaction: discord.Interaction, bet: int, mines: int = 3):
        if not (1 <= mines <= 15):
            await interaction.response.send_message("❌ 지뢰는 1-15개 사이만 설정 가능합니다!", ephemeral=True)
            return

        if mines >= 24:  # Max 24 mines in 5x5 grid (need at least 1 gem)
            await interaction.response.send_message("❌ 5x5 보드에서는 최대 15개의 지뢰만 설정 가능합니다!", ephemeral=True)
            return

        can_start, error_msg = await self.validate_game(interaction, bet)
        if not can_start:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        coins_cog = self.bot.get_cog('CoinsCog')
        if not await coins_cog.remove_coins(interaction.user.id, bet, "minesweeper_bet", "지뢰찾기 베팅"):
            await interaction.response.send_message("❌ 베팅 처리 실패!", ephemeral=True)
            return

        # Create game view
        view = MinesweeperView(self.bot, interaction.user.id, bet, mines)
        embed = await view.create_game_embed()

        await interaction.response.send_message(embed=embed, view=view)
        self.logger.info(f"{interaction.user}가 {bet}코인, {mines}개 지뢰로 지뢰찾기 시작")


async def setup(bot):
    await bot.add_cog(MinesweeperCog(bot))