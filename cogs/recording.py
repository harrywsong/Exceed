import discord
from discord.ext import commands, tasks
import asyncio
import subprocess
import os
import json
import psutil
from datetime import datetime, timedelta
import logging
import signal
import shutil
import zipfile
import time


class RecordingView(discord.ui.View):
    def __init__(self, recording_id, recording_dir, bot_logger):
        super().__init__(timeout=None)
        self.recording_id = recording_id
        self.recording_dir = recording_dir
        self.bot_logger = bot_logger

    @discord.ui.button(label="Download Audio Files", style=discord.ButtonStyle.primary, emoji="📥")
    async def download_files(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        try:
            audio_files = []
            if os.path.exists(self.recording_dir):
                for file in os.listdir(self.recording_dir):
                    if file.endswith(('.wav', '.mp3', '.m4a')):
                        audio_files.append(os.path.join(self.recording_dir, file))

            if not audio_files:
                await interaction.followup.send("No audio files found in this recording.", ephemeral=True)
                return

            if len(audio_files) == 1:
                file_path = audio_files[0]
                if os.path.getsize(file_path) > 25 * 1024 * 1024:
                    await interaction.followup.send("File too large for Discord. Please use the zip option.",
                                                    ephemeral=True)
                    return

                file = discord.File(file_path)
                await interaction.followup.send("Here's your audio file:", file=file, ephemeral=True)
            else:
                zip_path = os.path.join(self.recording_dir, f"recording_{self.recording_id}.zip")

                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for file_path in audio_files:
                        zipf.write(file_path, os.path.basename(file_path))

                if os.path.getsize(zip_path) > 25 * 1024 * 1024:
                    await interaction.followup.send("Archive too large for Discord download.", ephemeral=True)
                    os.remove(zip_path)
                    return

                file = discord.File(zip_path)
                await interaction.followup.send("Here are your audio files:", file=file, ephemeral=True)
                os.remove(zip_path)

        except Exception as e:
            self.bot_logger.error(f"Download error: {e}")
            await interaction.followup.send("Error creating download. Files may have been moved or deleted.",
                                            ephemeral=True)

    @discord.ui.button(label="Delete Recording", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_recording(self, interaction: discord.Interaction, button: discord.ui.Button):
        confirm_view = ConfirmDeleteView(self.recording_id, self.recording_dir, self.bot_logger)
        await interaction.response.send_message(
            f"Are you sure you want to delete recording `{self.recording_id}`? This cannot be undone.",
            view=confirm_view,
            ephemeral=True
        )


class ConfirmDeleteView(discord.ui.View):
    def __init__(self, recording_id, recording_dir, bot_logger):
        super().__init__(timeout=30)
        self.recording_id = recording_id
        self.recording_dir = recording_dir
        self.bot_logger = bot_logger

    @discord.ui.button(label="Yes, Delete", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        try:
            if os.path.exists(self.recording_dir):
                shutil.rmtree(self.recording_dir)
                await interaction.followup.send(f"Recording `{self.recording_id}` has been deleted.", ephemeral=True)
                self.bot_logger.info(f"Recording {self.recording_id} manually deleted by user {interaction.user.id}")
            else:
                await interaction.followup.send("Recording directory not found. It may have already been deleted.",
                                                ephemeral=True)
        except Exception as e:
            self.bot_logger.error(f"Manual delete error: {e}")
            await interaction.followup.send("Error deleting recording.", ephemeral=True)

        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Deletion cancelled.", ephemeral=True)


class Recording(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.recordings = {}
        self.recordings_path = "./recordings"
        self.max_concurrent_recordings = 1
        os.makedirs(self.recordings_path, exist_ok=True)

        self.cleanup_old_recordings.start()
        self._cleanup_node_processes()

    def cog_unload(self):
        self.cleanup_old_recordings.cancel()
        self.bot.logger.info("Cleaning up Recording cog...")
        for guild_id, recording in self.recordings.items():
            try:
                recording['process'].terminate()
            except:
                pass
        self._cleanup_node_processes()

    @tasks.loop(hours=24)
    async def cleanup_old_recordings(self):
        try:
            cutoff_date = datetime.now() - timedelta(days=7)
            deleted_count = 0

            for item in os.listdir(self.recordings_path):
                item_path = os.path.join(self.recordings_path, item)
                if os.path.isdir(item_path):
                    try:
                        creation_time = datetime.fromtimestamp(os.path.getctime(item_path))
                        if creation_time < cutoff_date:
                            shutil.rmtree(item_path)
                            deleted_count += 1
                            self.bot.logger.info(f"Deleted old recording: {item}")
                    except Exception as e:
                        self.bot.logger.error(f"Error deleting old recording {item}: {e}")

            if deleted_count > 0:
                self.bot.logger.info(f"Cleanup complete: deleted {deleted_count} old recordings")

        except Exception as e:
            self.bot.logger.error(f"Cleanup task error: {e}")

    @cleanup_old_recordings.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    def _cleanup_node_processes(self):
        try:
            # Kill any existing voice recorder processes
            if os.name == 'nt':  # Windows
                subprocess.run(['taskkill', '/f', '/im', 'node.exe', '/fi', 'WINDOWTITLE eq voice_recorder*'],
                              check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:  # Unix/Linux/Mac
                subprocess.run(['pkill', '-f', 'voice_recorder.js'], check=False)
        except:
            pass

    def _check_system_resources(self):
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()

            if cpu_percent > 80:
                return False, f"CPU usage too high ({cpu_percent:.1f}%)"
            if memory.percent > 85:
                return False, f"Memory usage too high ({memory.percent:.1f}%)"
            return True, "OK"
        except:
            return True, "OK"

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
            await interaction.response.send_message("🔹 No active recording in this server.")
            return

        recording = self.recordings[interaction.guild.id]
        duration = datetime.now() - recording['start_time']
        duration_str = str(duration).split('.')[0]

        embed = discord.Embed(
            title="🔴 Recording Active",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.add_field(name="Channel", value=recording['channel'].name, inline=True)
        embed.add_field(name="Duration", value=duration_str, inline=True)
        embed.add_field(name="Recording ID", value=recording['id'], inline=True)
        embed.add_field(name="Connected Users", value=len(recording['channel'].members), inline=True)

        try:
            cpu = psutil.cpu_percent()
            memory = psutil.virtual_memory().percent
            embed.add_field(name="System Load", value=f"CPU: {cpu:.1f}% | RAM: {memory:.1f}%", inline=False)
        except:
            pass

        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="list-recordings", description="List available recordings")
    async def list_recordings(self, interaction: discord.Interaction):
        await interaction.response.defer()

        try:
            recordings_found = []
            if os.path.exists(self.recordings_path):
                for item in os.listdir(self.recordings_path):
                    item_path = os.path.join(self.recordings_path, item)
                    if os.path.isdir(item_path):
                        audio_count = len([f for f in os.listdir(item_path)
                                           if f.endswith(('.wav', '.mp3', '.m4a'))])

                        creation_time = datetime.fromtimestamp(os.path.getctime(item_path))
                        recordings_found.append({
                            'id': item,
                            'path': item_path,
                            'files': audio_count,
                            'date': creation_time
                        })

            if not recordings_found:
                await interaction.followup.send("No recordings found.")
                return

            recordings_found.sort(key=lambda x: x['date'], reverse=True)

            embed = discord.Embed(
                title="📁 Available Recordings",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )

            for recording in recordings_found[:10]:
                age = datetime.now() - recording['date']
                age_str = f"{age.days}d {age.seconds // 3600}h ago" if age.days > 0 else f"{age.seconds // 3600}h {(age.seconds // 60) % 60}m ago"

                embed.add_field(
                    name=f"🎵 {recording['id']}",
                    value=f"Files: {recording['files']} | Created: {age_str}",
                    inline=False
                )

            if recordings_found:
                latest_recording = recordings_found[0]
                view = RecordingView(latest_recording['id'], latest_recording['path'], self.bot.logger)
                embed.set_footer(text=f"Showing download options for latest recording: {latest_recording['id']}")
                await interaction.followup.send(embed=embed, view=view)
            else:
                await interaction.followup.send(embed=embed)

        except Exception as e:
            self.bot.logger.error(f"List recordings error: {e}")
            await interaction.followup.send("Error listing recordings.")

    async def _start_recording(self, interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ You need to be in a voice channel!", ephemeral=True)
            return

        if interaction.guild.id in self.recordings:
            await interaction.response.send_message("❌ Already recording in this server!", ephemeral=True)
            return

        can_record, reason = self._check_system_resources()
        if not can_record:
            await interaction.response.send_message(f"❌ Cannot start recording: {reason}", ephemeral=True)
            return

        if len(self.recordings) >= self.max_concurrent_recordings:
            await interaction.response.send_message("❌ Maximum recordings reached for this system", ephemeral=True)
            return

        channel = interaction.user.voice.channel
        recording_id = str(int(datetime.now().timestamp()))
        recording_dir = os.path.join(self.recordings_path, recording_id)
        os.makedirs(recording_dir, exist_ok=True)

        await interaction.response.defer()

        try:
            self.bot.logger.info(f"Starting recording - Guild: {interaction.guild.name} ({interaction.guild.id})")
            self.bot.logger.info(f"Channel: {channel.name} ({channel.id})")
            self.bot.logger.info(f"User: {interaction.user.display_name} ({interaction.user.id})")

            # Check bot permissions
            bot_member = interaction.guild.get_member(self.bot.user.id)
            if bot_member:
                permissions = channel.permissions_for(bot_member)
                self.bot.logger.info(f"Bot permissions - Connect: {permissions.connect}, Speak: {permissions.speak}")
                if not permissions.connect or not permissions.speak:
                    await interaction.followup.send("❌ Bot lacks required permissions in voice channel!",
                                                    ephemeral=True)
                    return

            # Start Node.js recorder process
            env = os.environ.copy()
            env['DISCORD_BOT_TOKEN'] = self.bot.http.token

            # Create a new console window for the recorder process (Windows) or use nohup (Unix)
            creationflags = 0
            if os.name == 'nt':  # Windows
                creationflags = subprocess.CREATE_NEW_CONSOLE
                cmd = ['node', 'utils/voice_recorder.js', 'start',
                       str(interaction.guild.id), str(channel.id), recording_dir]
            else:  # Unix/Linux/Mac
                cmd = ['nohup', 'node', 'utils/voice_recorder.js', 'start',
                       str(interaction.guild.id), str(channel.id), recording_dir, '&']

            process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creationflags
            )

            # Wait a bit for the process to start
            await asyncio.sleep(3)

            # Check if process is still running
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                self.bot.logger.error(f"Recorder process failed immediately:")
                self.bot.logger.error(f"Exit code: {process.returncode}")
                self.bot.logger.error(f"Stdout: {stdout}")
                self.bot.logger.error(f"Stderr: {stderr}")
                await interaction.followup.send(f"❌ Recording process failed to start. Check bot logs for details.",
                                                ephemeral=True)
                return

            # Store recording info
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
            self.bot.logger.error(f"Recording start error: {e}", exc_info=True)
            if interaction.guild.id in self.recordings:
                del self.recordings[interaction.guild.id]
            await interaction.followup.send(f"❌ Failed to start recording: {str(e)[:100]}...", ephemeral=True)

    async def _stop_recording(self, interaction):
        if interaction.guild.id not in self.recordings:
            await interaction.response.send_message("❌ No active recording in this server!", ephemeral=True)
            return

        recording = self.recordings[interaction.guild.id]
        await interaction.response.defer()

        try:
            self.bot.logger.info(f"Stopping recording for guild {interaction.guild.id}")

            # Send stop command to the recorder process
            if recording['process'].poll() is None:
                self.bot.logger.info("Sending stop command to recorder")

                # Send stop command
                stop_env = dict(os.environ, DISCORD_BOT_TOKEN=self.bot.http.token)
                stop_process = subprocess.Popen([
                    'node', 'utils/voice_recorder.js', 'stop', str(interaction.guild.id)
                ], env=stop_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

                try:
                    stdout, stderr = await asyncio.wait_for(
                        asyncio.create_task(asyncio.to_thread(stop_process.communicate)),
                        timeout=15.0
                    )
                    self.bot.logger.info(f"Stop command output: {stdout}")
                    if stderr:
                        self.bot.logger.warning(f"Stop command stderr: {stderr}")
                except asyncio.TimeoutError:
                    stop_process.terminate()
                    self.bot.logger.warning("Stop command timed out")

            # Wait for files to be processed
            await asyncio.sleep(5)

            # Count audio files
            files_created = []
            if os.path.exists(recording['dir']):
                all_files = os.listdir(recording['dir'])
                files_created = [f for f in all_files if f.endswith(('.wav', '.mp3', '.m4a'))]

            duration = datetime.now() - recording['start_time']
            duration_str = str(duration).split('.')[0]

            embed = discord.Embed(
                title="✅ Recording Stopped",
                description="Recording completed successfully",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Duration", value=duration_str, inline=True)
            embed.add_field(name="Recording ID", value=f"`{recording['id']}`", inline=True)
            embed.add_field(name="Total Files", value=f"{len(files_created)} audio files", inline=True)
            embed.add_field(name="Location", value=f"`./recordings/{recording['id']}/`", inline=False)

            if files_created:
                file_list = '\n'.join([f"• {f}" for f in files_created[:10]])
                if len(files_created) > 10:
                    file_list += f"\n• ... and {len(files_created) - 10} more"
                embed.add_field(name="Audio Files", value=f"```{file_list}```", inline=False)
                embed.color = discord.Color.green()
            else:
                embed.add_field(name="Status", value="❌ No audio files were created", inline=False)
                embed.color = discord.Color.red()
                embed.add_field(
                    name="Possible Issues",
                    value="• No one spoke during recording\n• FFmpeg not installed\n• Permission issues",
                    inline=False
                )

            # Create view for download/delete options if files exist
            if files_created:
                view = RecordingView(recording['id'], recording['dir'], self.bot.logger)
                await interaction.followup.send(embed=embed, view=view)
            else:
                await interaction.followup.send(embed=embed)

            # Remove from active recordings
            del self.recordings[interaction.guild.id]

        except Exception as e:
            self.bot.logger.error(f"Recording stop error: {e}", exc_info=True)
            if interaction.guild.id in self.recordings:
                try:
                    self.recordings[interaction.guild.id]['process'].terminate()
                except:
                    pass
                del self.recordings[interaction.guild.id]
            await interaction.followup.send("⚠️ Recording stopped but there may have been processing errors.",
                                            ephemeral=True)


async def setup(bot):
    await bot.add_cog(Recording(bot))