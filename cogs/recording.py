
import discord
from discord.ext import commands
import asyncio
import subprocess
import os
import json
import psutil
from datetime import datetime
import logging
import signal


class Recording(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.recordings = {}
        self.recordings_path = "./recordings"
        self.max_concurrent_recordings = 1  # Limit for Pi
        os.makedirs(self.recordings_path, exist_ok=True)

        # Cleanup any existing node processes on startup
        self._cleanup_node_processes()

    def _cleanup_node_processes(self):
        """Clean up any orphaned node processes"""
        try:
            subprocess.run(['pkill', '-f', 'voice_recorder.js'], check=False)
        except:
            pass

    def _check_system_resources(self):
        """Check if system can handle recording"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()

            if cpu_percent > 80:
                return False, f"CPU usage too high ({cpu_percent:.1f}%)"
            if memory.percent > 85:
                return False, f"Memory usage too high ({memory.percent:.1f}%)"
            return True, "OK"
        except:
            return True, "OK"  # If we can't check, assume it's fine

    @discord.app_commands.command(name="record", description="Start/stop recording the voice channel")
    @discord.app_commands.describe(action="Choose to start or stop recording")
    @discord.app_commands.choices(action=[
        discord.app_commands.Choice(name="start", value="start"),
        discord.app_commands.Choice(name="stop", value="stop")
    ])
    async def record(self, interaction: discord.Interaction, action: str):
        if action == "start":
            await self._start_recording(interaction)
        elif action == "stop":
            await self._stop_recording(interaction)

    @discord.app_commands.command(name="record-status", description="Check current recording status")
    async def recording_status(self, interaction: discord.Interaction):
        if interaction.guild.id not in self.recordings:
            await interaction.response.send_message("📹 No active recording in this server.")
            return

        recording = self.recordings[interaction.guild.id]
        duration = datetime.now() - recording['start_time']
        duration_str = str(duration).split('.')[0]  # Remove microseconds

        embed = discord.Embed(
            title="🔴 Recording Active",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.add_field(name="Channel", value=recording['channel'].name, inline=True)
        embed.add_field(name="Duration", value=duration_str, inline=True)
        embed.add_field(name="Recording ID", value=recording['id'], inline=True)
        embed.add_field(name="Connected Users", value=len(recording['channel'].members), inline=True)

        # System info
        try:
            cpu = psutil.cpu_percent()
            memory = psutil.virtual_memory().percent
            embed.add_field(name="System Load", value=f"CPU: {cpu:.1f}% | RAM: {memory:.1f}%", inline=False)
        except:
            pass

        await interaction.response.send_message(embed=embed)

    async def _start_recording(self, interaction):
        # Check if user is in voice channel
        if not interaction.user.voice:
            await interaction.response.send_message("❌ You need to be in a voice channel!", ephemeral=True)
            return

        # Check if already recording
        if interaction.guild.id in self.recordings:
            await interaction.response.send_message("❌ Already recording in this server!", ephemeral=True)
            return

        # Check system resources
        can_record, reason = self._check_system_resources()
        if not can_record:
            await interaction.response.send_message(f"❌ Cannot start recording: {reason}", ephemeral=True)
            return

        # Limit concurrent recordings on Pi
        if len(self.recordings) >= self.max_concurrent_recordings:
            await interaction.response.send_message("❌ Maximum recordings reached for this system", ephemeral=True)
            return

        channel = interaction.user.voice.channel

        # Add this debugging section after: channel = interaction.user.voice.channel
        try:
            # Log detailed channel information
            self.bot.logger.info(f"Debug - Guild ID: {interaction.guild.id}")
            self.bot.logger.info(f"Debug - Channel ID: {channel.id}")
            self.bot.logger.info(f"Debug - Channel Name: {channel.name}")
            self.bot.logger.info(f"Debug - User in channel: {interaction.user.display_name}")
            self.bot.logger.info(f"Debug - Bot can see channel: {channel is not None}")

            # Check bot permissions
            bot_member = interaction.guild.get_member(self.bot.user.id)
            if bot_member:
                permissions = channel.permissions_for(bot_member)
                self.bot.logger.info(
                    f"Debug - Bot permissions - Connect: {permissions.connect}, Speak: {permissions.speak}")

        except Exception as debug_error:
            self.bot.logger.error(f"Debug error: {debug_error}")
        recording_id = str(int(datetime.now().timestamp()))
        recording_dir = os.path.join(self.recordings_path, recording_id)
        os.makedirs(recording_dir, exist_ok=True)

        # Defer response since this might take a moment
        await interaction.response.defer()

        try:
            # Start Node.js recorder process
            env = os.environ.copy()
            env['DISCORD_BOT_TOKEN'] = self.bot.http.token

            process = subprocess.Popen([
                'node', 'utils/voice_recorder.js', 'start',
                str(interaction.guild.id), str(channel.id), recording_dir
            ], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Give it a moment to start
            await asyncio.sleep(3)

            # Check if process is still running
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise Exception(f"Recorder failed to start: {stderr.decode()}")

            self.recordings[interaction.guild.id] = {
                'id': recording_id,
                'process': process,
                'channel': channel,
                'start_time': datetime.now(),
                'dir': recording_dir
            }

            embed = discord.Embed(
                title="✅ Recording Started",
                description=f"Recording audio in {channel.name}",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Recording ID", value=f"`{recording_id}`", inline=True)
            embed.add_field(name="Output Directory", value=f"`./recordings/{recording_id}/`", inline=False)
            embed.set_footer(text="Use /record stop to end the recording")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logging.error(f"Recording start error: {e}")
            await interaction.followup.send("❌ Failed to start recording. Check bot permissions and system resources.",
                                            ephemeral=True)

    async def _stop_recording(self, interaction):
        if interaction.guild.id not in self.recordings:
            await interaction.response.send_message("❌ No active recording in this server!", ephemeral=True)
            return

        recording = self.recordings[interaction.guild.id]

        # Defer response since processing might take time
        await interaction.response.defer()

        try:
            # Stop the Node.js process gracefully
            stop_process = subprocess.Popen([
                'node', 'utils/voice_recorder.js', 'stop', str(interaction.guild.id)
            ], env=dict(os.environ, DISCORD_BOT_TOKEN=self.bot.http.token))

            # Wait for stop command to complete
            await asyncio.sleep(2)

            # Terminate the recording process if still running
            if recording['process'].poll() is None:
                recording['process'].terminate()
                try:
                    recording['process'].wait(timeout=5)
                except subprocess.TimeoutExpired:
                    recording['process'].kill()

            # Check what files were created
            files_created = []
            if os.path.exists(recording['dir']):
                files_created = [f for f in os.listdir(recording['dir']) if f.endswith('.wav')]

            duration = datetime.now() - recording['start_time']
            duration_str = str(duration).split('.')[0]

            embed = discord.Embed(
                title="✅ Recording Stopped",
                description=f"Recording completed successfully",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Duration", value=duration_str, inline=True)
            embed.add_field(name="Recording ID", value=f"`{recording['id']}`", inline=True)
            embed.add_field(name="Files Created", value=f"{len(files_created)} audio files", inline=True)
            embed.add_field(name="Location", value=f"`./recordings/{recording['id']}/`", inline=False)

            if files_created:
                file_list = '\n'.join([f"• {f}" for f in files_created[:10]])  # Show first 10 files
                if len(files_created) > 10:
                    file_list += f"\n• ... and {len(files_created) - 10} more"
                embed.add_field(name="Audio Files", value=f"```{file_list}```", inline=False)

            del self.recordings[interaction.guild.id]
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logging.error(f"Recording stop error: {e}")
            # Still try to cleanup
            if interaction.guild.id in self.recordings:
                try:
                    self.recordings[interaction.guild.id]['process'].terminate()
                except:
                    pass
                del self.recordings[interaction.guild.id]

            await interaction.followup.send("⚠️ Recording stopped but there may have been processing errors.",
                                            ephemeral=True)

    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        for guild_id, recording in self.recordings.items():
            try:
                recording['process'].terminate()
            except:
                pass
        self._cleanup_node_processes()


async def setup(bot):
    await bot.add_cog(Recording(bot))
