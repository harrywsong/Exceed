import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
import wave
import threading
from datetime import datetime, timedelta
import json
from typing import Dict, Optional, List
import traceback
import io

from utils.logger import get_logger
from utils import config


class AudioSink(discord.sinks.WaveSink):
    """Custom audio sink for individual user recording"""

    def __init__(self, *, filters=None):
        super().__init__(filters=filters)
        self.user_audio_data = {}
        self.recording_start = datetime.now()

    def write(self, data, user):
        """Override to capture audio data per user"""
        if user.id not in self.user_audio_data:
            self.user_audio_data[user.id] = {
                'user': user,
                'audio_data': io.BytesIO(),
                'packets': []
            }

        # Store raw audio packets for later processing
        self.user_audio_data[user.id]['packets'].append(data)

    def cleanup(self):
        """Process and save individual user audio files"""
        recording_dir = "recordings"
        os.makedirs(recording_dir, exist_ok=True)

        saved_files = []
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        for user_id, user_data in self.user_audio_data.items():
            user = user_data['user']
            packets = user_data['packets']

            if not packets:
                continue

            # Create filename with safe characters
            safe_username = "".join(c for c in user.display_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
            filename = f"{session_id}_{safe_username}_{user.id}.wav"
            filepath = os.path.join(recording_dir, filename)

            try:
                # Convert packets to audio data
                audio_data = b''.join(packets)

                if audio_data:
                    # Save as WAV file with proper Discord audio specs
                    with wave.open(filepath, 'wb') as wav_file:
                        wav_file.setnchannels(2)  # Stereo
                        wav_file.setsampwidth(2)  # 16-bit
                        wav_file.setframerate(48000)  # Discord's sample rate
                        wav_file.writeframes(audio_data)

                    saved_files.append({
                        'user': user,
                        'file': filepath,
                        'filename': filename,
                        'size': len(audio_data)
                    })

            except Exception as e:
                print(f"Error saving audio for {user.display_name}: {e}")

        return saved_files


class RecordingSession:
    """Manages a recording session"""

    def __init__(self, session_id: str, guild_id: int, channel_id: int, initiator: discord.Member):
        self.session_id = session_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.initiator = initiator
        self.start_time = datetime.now()
        self.end_time = None
        self.is_active = True
        self.participants = set()
        self.voice_client = None
        self.sink = None

    def add_participant(self, user: discord.Member):
        """Add a user to the session participants"""
        self.participants.add(user)

    def get_duration(self) -> str:
        """Get the duration of the recording session"""
        end = self.end_time if self.end_time else datetime.now()
        duration = end - self.start_time
        return str(duration).split('.')[0]  # Remove microseconds

    def to_dict(self) -> dict:
        """Convert session to dictionary for storage"""
        return {
            'session_id': self.session_id,
            'guild_id': self.guild_id,
            'channel_id': self.channel_id,
            'initiator_id': self.initiator.id,
            'initiator_name': str(self.initiator),
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'duration': self.get_duration(),
            'participants': [
                {'id': user.id, 'name': str(user), 'display_name': user.display_name}
                for user in self.participants
            ]
        }


class AudioRecording(commands.Cog):
    """Voice recording functionality using py-cord"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger(
            "음성 녹음",
            bot=self.bot,
            discord_log_channel_id=config.LOG_CHANNEL_ID
        )

        # Active recording sessions per guild
        self.active_sessions: Dict[int, RecordingSession] = {}

        # Recording storage directory
        self.recording_dir = "recordings"
        os.makedirs(self.recording_dir, exist_ok=True)

        # Check if py-cord is available
        try:
            hasattr(discord.sinks, 'WaveSink')
            self.logger.info("py-cord 음성 녹음 기능이 감지되었습니다.")
        except AttributeError:
            self.logger.error("py-cord가 설치되지 않았습니다. 음성 녹음이 불가능합니다.")
            raise

        self.logger.info("음성 녹음 기능이 초기화되었습니다.")

    def cog_unload(self):
        """Clean up when cog is unloaded"""
        # Stop all active recordings
        for session in list(self.active_sessions.values()):
            try:
                asyncio.create_task(self._stop_recording_internal(session))
            except Exception as e:
                self.logger.error(f"Error stopping recording during cog unload: {e}")

        self.logger.info("녹음 Cog 언로드됨")

    @app_commands.command(name="녹음시작", description="현재 음성 채널에서 녹음을 시작합니다")
    async def start_recording(self, interaction: discord.Interaction):
        """Start recording in the current voice channel"""

        # Check if user is in a voice channel
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "❌ 음성 채널에 먼저 입장해주세요!", ephemeral=True
            )
            return

        voice_channel = interaction.user.voice.channel
        guild_id = interaction.guild.id

        # Check if already recording in this guild
        if guild_id in self.active_sessions:
            session = self.active_sessions[guild_id]
            await interaction.response.send_message(
                f"❌ 이미 녹음 중입니다! (세션 ID: `{session.session_id}`)\n"
                f"`/녹음중지` 명령어로 현재 녹음을 중지하세요.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # Check voice permissions
            permissions = voice_channel.permissions_for(interaction.guild.me)
            if not permissions.connect or not permissions.speak:
                await interaction.followup.send(
                    "❌ 음성 채널에 연결하거나 말할 권한이 없습니다. 봇 권한을 확인해주세요.",
                    ephemeral=True
                )
                return

            # Connect to voice channel
            if interaction.guild.voice_client:
                if interaction.guild.voice_client.channel != voice_channel:
                    await interaction.guild.voice_client.move_to(voice_channel)
                voice_client = interaction.guild.voice_client
            else:
                voice_client = await voice_channel.connect()

            # Wait a moment for connection to stabilize
            await asyncio.sleep(1)

            # Create recording session
            session_id = f"{guild_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            session = RecordingSession(
                session_id=session_id,
                guild_id=guild_id,
                channel_id=voice_channel.id,
                initiator=interaction.user
            )

            # Add current channel members as participants
            for member in voice_channel.members:
                if not member.bot:
                    session.add_participant(member)

            # Set up recording
            session.voice_client = voice_client
            session.sink = AudioSink()

            # Start recording
            voice_client.start_recording(
                session.sink,
                self._recording_finished_callback,
                session
            )

            self.active_sessions[guild_id] = session

            # Create success embed
            embed = discord.Embed(
                title="🎙️ 녹음 시작!",
                description=f"**{voice_channel.name}** 채널에서 녹음이 시작되었습니다.",
                color=discord.Color.green(),
                timestamp=session.start_time
            )

            participants_text = "\n".join([
                f"• {user.display_name}" for user in session.participants
            ])

            embed.add_field(
                name="📊 녹음 정보",
                value=(
                    f"**세션 ID:** `{session_id}`\n"
                    f"**시작 시간:** <t:{int(session.start_time.timestamp())}:F>\n"
                    f"**참가자:** {len(session.participants)}명"
                ),
                inline=False
            )

            if participants_text:
                embed.add_field(
                    name="👥 현재 참가자",
                    value=participants_text[:1024],  # Discord field limit
                    inline=False
                )

            embed.set_footer(text="/녹음중지 명령어로 녹음을 중지할 수 있습니다")

            await interaction.followup.send(embed=embed, ephemeral=True)

            self.logger.info(
                f"✅ {interaction.user.display_name}님이 {voice_channel.name}에서 녹음을 시작했습니다. "
                f"세션 ID: {session_id}, 참가자: {len(session.participants)}명"
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ 음성 채널에 연결할 권한이 없습니다. 봇 권한을 확인해주세요.",
                ephemeral=True
            )
            self.logger.error(f"❌ 음성 채널 연결 권한 부족: {voice_channel.name}")

        except Exception as e:
            await interaction.followup.send(
                f"❌ 녹음 시작 중 오류가 발생했습니다: `{str(e)[:100]}...`",
                ephemeral=True
            )
            self.logger.error(f"❌ 녹음 시작 실패: {e}\n{traceback.format_exc()}")

    @app_commands.command(name="녹음중지", description="현재 진행 중인 녹음을 중지합니다")
    async def stop_recording(self, interaction: discord.Interaction):
        """Stop the current recording"""

        guild_id = interaction.guild.id

        if guild_id not in self.active_sessions:
            await interaction.response.send_message(
                "❌ 현재 진행 중인 녹음이 없습니다.", ephemeral=True
            )
            return

        session = self.active_sessions[guild_id]

        # Check permissions (only initiator or users with manage_channels can stop)
        if (interaction.user != session.initiator and
                not interaction.user.guild_permissions.manage_channels):
            await interaction.response.send_message(
                f"❌ 녹음을 중지할 권한이 없습니다. 녹음 시작자({session.initiator.display_name}) 또는 "
                f"채널 관리 권한이 있는 사용자만 중지할 수 있습니다.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            result = await self._stop_recording_internal(session)

            if result['success']:
                embed = discord.Embed(
                    title="⏹️ 녹음 완료!",
                    description=f"세션 **{session.session_id}**가 종료되었습니다.",
                    color=discord.Color.red(),
                    timestamp=datetime.now()
                )

                embed.add_field(
                    name="📊 녹음 정보",
                    value=(
                        f"**지속 시간:** {session.get_duration()}\n"
                        f"**참가자:** {len(session.participants)}명\n"
                        f"**저장된 파일:** {len(result.get('files', []))}개"
                    ),
                    inline=False
                )

                if result.get('files'):
                    file_list = "\n".join([
                        f"• {file_info['user'].display_name} - {file_info['filename']}"
                        for file_info in result['files'][:10]  # Limit to 10 files shown
                    ])

                    if len(result['files']) > 10:
                        file_list += f"\n... 그 외 {len(result['files']) - 10}개 파일"

                    embed.add_field(
                        name="💾 저장된 파일",
                        value=file_list,
                        inline=False
                    )

                embed.set_footer(text="서버의 recordings/ 폴더에 파일이 저장되었습니다")

                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(
                    f"❌ 녹음 중지 중 오류가 발생했습니다: {result.get('error', '알 수 없는 오류')}",
                    ephemeral=True
                )

        except Exception as e:
            await interaction.followup.send(
                f"❌ 녹음 중지 중 예상치 못한 오류가 발생했습니다: `{str(e)[:100]}...`",
                ephemeral=True
            )
            self.logger.error(f"❌ 녹음 중지 실패: {e}\n{traceback.format_exc()}")

    @app_commands.command(name="녹음상태", description="현재 녹음 상태를 확인합니다")
    async def recording_status(self, interaction: discord.Interaction):
        """Check current recording status"""

        guild_id = interaction.guild.id

        if guild_id not in self.active_sessions:
            embed = discord.Embed(
                title="📊 녹음 상태",
                description="현재 진행 중인 녹음이 없습니다.",
                color=discord.Color.blue()
            )
        else:
            session = self.active_sessions[guild_id]
            channel = self.bot.get_channel(session.channel_id)

            embed = discord.Embed(
                title="📊 녹음 상태",
                description="🔴 **녹음 진행 중**",
                color=discord.Color.green(),
                timestamp=session.start_time
            )

            embed.add_field(
                name="🎙️ 녹음 정보",
                value=(
                    f"**세션 ID:** `{session.session_id}`\n"
                    f"**채널:** {channel.name if channel else '알 수 없음'}\n"
                    f"**시작자:** {session.initiator.display_name}\n"
                    f"**지속 시간:** {session.get_duration()}"
                ),
                inline=False
            )

            # Get current participants in voice channel
            current_participants = []
            if channel and isinstance(channel, discord.VoiceChannel):
                current_participants = [m for m in channel.members if not m.bot]

            embed.add_field(
                name="👥 참가자 정보",
                value=(
                    f"**총 참가자:** {len(session.participants)}명\n"
                    f"**현재 채널:** {len(current_participants)}명"
                ),
                inline=True
            )

            embed.set_footer(text="녹음 시작 시간")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="녹음목록", description="최근 녹음 기록을 확인합니다")
    @app_commands.describe(limit="표시할 녹음 수 (기본값: 5)")
    async def recording_list(self, interaction: discord.Interaction, limit: int = 5):
        """List recent recordings"""

        await interaction.response.defer(ephemeral=True)

        try:
            # Find metadata files
            metadata_files = []
            if os.path.exists(self.recording_dir):
                for filename in os.listdir(self.recording_dir):
                    if filename.endswith('_metadata.json'):
                        filepath = os.path.join(self.recording_dir, filename)
                        try:
                            stat = os.stat(filepath)
                            metadata_files.append((filepath, stat.st_mtime))
                        except OSError:
                            continue

            if not metadata_files:
                embed = discord.Embed(
                    title="📂 녹음 기록",
                    description="아직 완료된 녹음이 없습니다.",
                    color=discord.Color.blue()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Sort by modification time (newest first)
            metadata_files.sort(key=lambda x: x[1], reverse=True)

            limit = max(1, min(limit, 10))  # Limit between 1 and 10

            embed = discord.Embed(
                title="📂 최근 녹음 기록",
                description=f"최근 {min(limit, len(metadata_files))}개의 녹음",
                color=discord.Color.blue()
            )

            for i, (filepath, _) in enumerate(metadata_files[:limit]):
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)

                    start_time = datetime.fromisoformat(metadata['start_time'])
                    duration = metadata.get('duration', '알 수 없음')
                    participants = metadata.get('participants', [])

                    embed.add_field(
                        name=f"🎤 {metadata['session_id']}",
                        value=(
                            f"**시작:** <t:{int(start_time.timestamp())}:R>\n"
                            f"**지속:** {duration}\n"
                            f"**참가자:** {len(participants)}명"
                        ),
                        inline=True
                    )

                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    self.logger.error(f"메타데이터 파일 읽기 오류 {filepath}: {e}")
                    continue

            embed.set_footer(text=f"총 {len(metadata_files)}개의 녹음 기록")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(
                f"❌ 녹음 목록 조회 중 오류가 발생했습니다: `{str(e)[:100]}...`",
                ephemeral=True
            )
            self.logger.error(f"❌ 녹음 목록 조회 실패: {e}\n{traceback.format_exc()}")

    async def _stop_recording_internal(self, session: RecordingSession) -> dict:
        """Internal method to stop recording and save files"""
        try:
            session.end_time = datetime.now()
            session.is_active = False

            # Stop recording
            if session.voice_client and session.voice_client.is_connected():
                session.voice_client.stop_recording()

                # Wait a bit for recording to finish
                await asyncio.sleep(3)

                # Disconnect from voice
                await session.voice_client.disconnect()

            # Process and save audio files
            saved_files = []
            if session.sink:
                saved_files = session.sink.cleanup()

            # Save metadata
            metadata_path = os.path.join(
                self.recording_dir,
                f"{session.session_id}_metadata.json"
            )

            try:
                with open(metadata_path, 'w', encoding='utf-8') as f:
                    json.dump(session.to_dict(), f, indent=2, ensure_ascii=False)
            except Exception as e:
                self.logger.error(f"메타데이터 저장 실패: {e}")

            # Remove from active sessions
            if session.guild_id in self.active_sessions:
                del self.active_sessions[session.guild_id]

            self.logger.info(
                f"✅ 녹음 완료: {session.session_id}, 지속시간: {session.get_duration()}, "
                f"저장된 파일: {len(saved_files)}개"
            )

            return {
                'success': True,
                'files': saved_files,
                'session': session
            }

        except Exception as e:
            self.logger.error(f"❌ 녹음 중지 처리 실패: {e}\n{traceback.format_exc()}")
            return {
                'success': False,
                'error': str(e)
            }

    def _recording_finished_callback(self, sink: AudioSink, session: RecordingSession):
        """Callback when recording finishes (for unexpected disconnections)"""
        try:
            if session.guild_id in self.active_sessions:
                self.logger.info(f"녹음이 예상치 못하게 종료되었습니다: {session.session_id}")
                # The cleanup will be handled by the sink cleanup
                asyncio.create_task(self._stop_recording_internal(session))
        except Exception as e:
            self.logger.error(f"녹음 완료 콜백 오류: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle users joining/leaving voice channels during recording"""
        if member.bot:
            return

        # Check if there's an active recording in this guild
        guild_id = member.guild.id
        if guild_id not in self.active_sessions:
            return

        session = self.active_sessions[guild_id]
        recording_channel = self.bot.get_channel(session.channel_id)

        if not recording_channel:
            return

        # User joined the recording channel
        if after.channel == recording_channel and before.channel != recording_channel:
            session.add_participant(member)
            self.logger.info(
                f"👤 {member.display_name}님이 녹음 중인 채널에 참가했습니다: {session.session_id}"
            )

        # User left the recording channel
        elif before.channel == recording_channel and after.channel != recording_channel:
            self.logger.info(
                f"👋 {member.display_name}님이 녹음 중인 채널에서 나갔습니다: {session.session_id}"
            )

            # Check if recording channel is now empty (except bot)
            remaining_users = [m for m in recording_channel.members if not m.bot]
            if len(remaining_users) == 0:
                self.logger.info(f"녹음 채널이 비어서 자동으로 녹음을 중지합니다: {session.session_id}")
                await self._stop_recording_internal(session)


async def setup(bot):
    await bot.add_cog(AudioRecording(bot))