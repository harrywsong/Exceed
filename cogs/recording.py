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
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account
import pickle
from google.auth.transport.requests import Request

# Google Drive API setup
SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'exceed-interview-sheet-992849d383a9.json'  # Path to your service account credentials


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

    async def _upload_to_drive(self, folder_path, recording_id):
        """Upload a folder to Google Drive"""
        try:
            # Authenticate with service account
            creds = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES)

            drive_service = build('drive', 'v3', credentials=creds)

            # Create folder in Google Drive
            folder_metadata = {
                'name': f'recording_{recording_id}',
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': ['1p-RdA-_iNNTJAkzD6jgPMrQsPGv2LGxA']  # Target folder ID
            }

            folder = drive_service.files().create(body=folder_metadata, fields='id').execute()
            folder_id = folder.get('id')

            self.bot.logger.info(f"Created Google Drive folder: {folder_id}")

            # Upload all files in the recording directory
            uploaded_files = []
            for file_name in os.listdir(folder_path):
                if file_name.endswith(('.wav', '.mp3', '.m4a')):
                    file_path = os.path.join(folder_path, file_name)

                    file_metadata = {
                        'name': file_name,
                        'parents': [folder_id]
                    }

                    media = MediaFileUpload(file_path, resumable=True)

                    # Upload with longer timeout and retry mechanism
                    file = None
                    for attempt in range(5):  # Try up to 5 times
                        try:
                            file = drive_service.files().create(
                                body=file_metadata,
                                media_body=media,
                                fields='id'
                            ).execute()
                            break
                        except Exception as e:
                            if attempt < 4:
                                self.bot.logger.warning(
                                    f"Upload attempt {attempt + 1} failed: {e}. Retrying in 5 seconds...")
                                await asyncio.sleep(5)
                            else:
                                raise e

                    uploaded_files.append(file.get('id'))
                    self.bot.logger.info(f"Uploaded {file_name} to Google Drive")

            return folder_id, uploaded_files

        except Exception as e:
            self.bot.logger.error(f"Google Drive upload error: {e}")
            raise

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

    async def _start_recording(self, interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("⛔ You need to be in a voice channel!", ephemeral=True)
            return

        if interaction.guild.id in self.recordings:
            await interaction.response.send_message("⛔ Already recording in this server!", ephemeral=True)
            return

        can_record, reason = self._check_system_resources()
        if not can_record:
            await interaction.response.send_message(f"⛔ Cannot start recording: {reason}", ephemeral=True)
            return

        if len(self.recordings) >= self.max_concurrent_recordings:
            await interaction.response.send_message("⛔ Maximum recordings reached for this system", ephemeral=True)
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
                    await interaction.followup.send("⛔ Bot lacks required permissions in voice channel!",
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
                await interaction.followup.send(f"⛔ Recording process failed to start. Check bot logs for details.",
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
                description=f"Recording continuous tracks in {channel.name}",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Recording ID", value=f"`{recording_id}`", inline=True)
            embed.add_field(name="Output Directory", value=f"`./recordings/{recording_id}/`", inline=False)
            embed.add_field(name="Track Type", value="Continuous per-user tracks with sync", inline=False)
            embed.set_footer(text="Use /record stop to end the recording")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            self.bot.logger.error(f"Recording start error: {e}", exc_info=True)
            if interaction.guild.id in self.recordings:
                del self.recordings[interaction.guild.id]
            await interaction.followup.send(f"⛔ Failed to start recording: {str(e)[:100]}...", ephemeral=True)

    async def _stop_recording(self, interaction):
        if interaction.guild.id not in self.recordings:
            await interaction.response.send_message("⛔ No active recording in this server!", ephemeral=True)
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
                    # Increased timeout for continuous track processing
                    stdout, stderr = await asyncio.wait_for(
                        asyncio.create_task(asyncio.to_thread(stop_process.communicate)),
                        timeout=30.0  # Increased from 15 to 30 seconds
                    )
                    self.bot.logger.info(f"Stop command output: {stdout}")
                    if stderr:
                        self.bot.logger.warning(f"Stop command stderr: {stderr}")
                except asyncio.TimeoutError:
                    stop_process.terminate()
                    self.bot.logger.warning("Stop command timed out")

            # Wait longer for continuous tracks to be processed
            max_wait_time = 120  # Increased to 2 minutes for larger files
            check_interval = 10
            files_created = []

            for i in range(0, max_wait_time, check_interval):
                await asyncio.sleep(check_interval)

                if os.path.exists(recording['dir']):
                    all_files = os.listdir(recording['dir'])
                    # Look specifically for continuous track files
                    continuous_files = [f for f in all_files if
                                        f.endswith(('.wav', '.mp3', '.m4a')) and
                                        'continuous' in f and
                                        not f.startswith('stop')]

                    self.bot.logger.info(
                        f"Check {i // check_interval + 1}: Found {len(continuous_files)} continuous track files")

                    for f in continuous_files:
                        file_path = os.path.join(recording['dir'], f)
                        if os.path.exists(file_path):
                            size = os.path.getsize(file_path)
                            self.bot.logger.info(f"  - {f}: {size} bytes")

                    # Check if files are stable (not growing)
                    if continuous_files and i < max_wait_time - check_interval:
                        await asyncio.sleep(check_interval)
                        stable_files = []
                        for f in continuous_files:
                            file_path = os.path.join(recording['dir'], f)
                            if os.path.exists(file_path):
                                new_size = os.path.getsize(file_path)
                                if new_size > 1000:  # Accept files > 1KB (continuous tracks will be larger)
                                    stable_files.append(f)

                        if stable_files:
                            files_created = stable_files
                            break
                    elif continuous_files:
                        files_created = [f for f in continuous_files if
                                         os.path.getsize(os.path.join(recording['dir'], f)) > 1000]
                        break

            # Final comprehensive check
            if not files_created and os.path.exists(recording['dir']):
                all_files = os.listdir(recording['dir'])
                self.bot.logger.info(f"Final check - All files in directory: {all_files}")

                # Look for ANY audio files for debugging
                for f in all_files:
                    if f.endswith(('.wav', '.mp3', '.m4a')):
                        file_path = os.path.join(recording['dir'], f)
                        size = os.path.getsize(file_path)
                        self.bot.logger.info(f"Audio file found: {f} ({size} bytes)")
                        if size > 1000:  # Accept larger files for continuous tracks
                            files_created.append(f)

            duration = datetime.now() - recording['start_time']
            duration_str = str(duration).split('.')[0]

            # Upload to Google Drive
            upload_embed = discord.Embed(
                title="📤 Uploading to Google Drive",
                description="Please wait while we upload your recording...",
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )
            upload_embed.add_field(name="Recording ID", value=f"`{recording['id']}`", inline=True)
            upload_embed.add_field(name="Files", value=f"{len(files_created)} tracks to upload", inline=True)
            upload_embed.set_footer(text="This may take several minutes for large recordings")

            await interaction.followup.send(embed=upload_embed)

            # Upload to Google Drive
            drive_folder_id = None
            try:
                drive_folder_id, uploaded_files = await self._upload_to_drive(recording['dir'], recording['id'])
                self.bot.logger.info(
                    f"Successfully uploaded {len(uploaded_files)} files to Google Drive folder {drive_folder_id}")
            except Exception as e:
                self.bot.logger.error(f"Failed to upload to Google Drive: {e}")
                drive_folder_id = None

            # Create final status embed
            embed = discord.Embed(
                title="✅ Recording Stopped",
                description="Continuous track recording completed",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Duration", value=duration_str, inline=True)
            embed.add_field(name="Recording ID", value=f"`{recording['id']}`", inline=True)
            embed.add_field(name="Track Files", value=f"{len(files_created)} continuous tracks", inline=True)

            if drive_folder_id:
                embed.add_field(
                    name="📁 Google Drive",
                    value=f"[View Recording Folder](https://drive.google.com/drive/folders/{drive_folder_id})",
                    inline=False
                )
                embed.color = discord.Color.green()
            else:
                embed.add_field(
                    name="⚠️ Upload Status",
                    value="Failed to upload to Google Drive. Files are available locally.",
                    inline=False
                )
                embed.color = discord.Color.red()

            if files_created:
                file_list = '\n'.join([f"• {f}" for f in files_created[:5]])
                if len(files_created) > 5:
                    file_list += f"\n• ... and {len(files_created) - 5} more"
                embed.add_field(name="Track Files", value=f"```{file_list}```", inline=False)

                # Note about continuous tracks
                embed.add_field(
                    name="ℹ️ Track Information",
                    value="Each file contains a continuous track for one user, synchronized from recording start to end with silence during absence periods.",
                    inline=False
                )
            else:
                embed.add_field(name="Status", value="⛔ No track files were created", inline=False)
                embed.color = discord.Color.red()

            await interaction.edit_original_response(embed=embed)

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

            error_embed = discord.Embed(
                title="❌ Recording Error",
                description="An error occurred while processing your recording.",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            error_embed.add_field(name="Error", value=str(e)[:200], inline=False)

            await interaction.edit_original_response(embed=error_embed)


async def setup(bot):
    await bot.add_cog(Recording(bot))