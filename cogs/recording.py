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

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account
import pickle
from google.auth.transport.requests import Request

# Google Drive API 설정 - OAuth 2.0
SCOPES = ['https://www.googleapis.com/auth/drive.file']
CREDENTIALS_FILE = 'exceed-465801-9a237edcd3b1.json'  # 실제 OAuth 자격증명 파일명
TOKEN_FILE = 'token.pickle'  # 기존 토큰 파일
TARGET_FOLDER_ID = "1p-RdA-_iNNTJAkzD6jgPMrQsPGv2LGxA"  # 대상 폴더 ID


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
        self.bot.logger.info("녹음 코그 정리 중...")

        # 모든 활성 녹음에 대해 봇 닉네임 복원 시도
        for guild_id, recording in self.recordings.items():
            try:
                recording['process'].terminate()

                # 봇 닉네임 복원
                guild = self.bot.get_guild(guild_id)
                if guild:
                    bot_member = guild.get_member(self.bot.user.id)
                    if bot_member:
                        original_nickname = recording.get('original_nickname', None)
                        if original_nickname and original_nickname != "(음성 녹화중) 아날로그":
                            asyncio.create_task(bot_member.edit(nick=original_nickname))
                        else:
                            asyncio.create_task(bot_member.edit(nick=None))
                        self.bot.logger.info(f"길드 {guild_id}에서 봇 닉네임 복원")
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
                            self.bot.logger.info(f"오래된 녹음 삭제됨: {item}")
                    except Exception as e:
                        self.bot.logger.error(f"오래된 녹음 {item} 삭제 오류: {e}")

            if deleted_count > 0:
                self.bot.logger.info(f"정리 완료: {deleted_count}개의 오래된 녹음 삭제됨")

        except Exception as e:
            self.bot.logger.error(f"정리 작업 오류: {e}")

    @cleanup_old_recordings.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    def _cleanup_node_processes(self):
        try:
            # 기존 음성 녹음기 프로세스 종료
            if os.name == 'nt':  # Windows
                subprocess.run(['taskkill', '/f', '/im', 'node.exe', '/fi', 'WINDOWTITLE eq voice_recorder*'],
                               check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:  # Unix/Linux/Mac
                subprocess.run(['pkill', '-f', 'voice_recorder.js'], check=False)
        except:
            pass

    def _get_oauth_credentials(self):
        """기존 token.pickle에서 OAuth 2.0 자격증명 가져오기"""
        creds = None

        # 자격증명 파일 존재 확인
        if not os.path.exists(CREDENTIALS_FILE):
            self.bot.logger.error(f"OAuth 자격증명 파일을 찾을 수 없음: {CREDENTIALS_FILE}")
            raise FileNotFoundError(f"OAuth 자격증명 파일을 찾을 수 없음: {CREDENTIALS_FILE}")

        # 기존 토큰이 있으면 로드
        if os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE, 'rb') as token:
                    creds = pickle.load(token)
                self.bot.logger.info("token.pickle에서 기존 OAuth 토큰 로드됨")
            except Exception as e:
                self.bot.logger.error(f"token.pickle 로드 오류: {e}")
                creds = None

        # 유효한 자격증명이 없으면 OAuth 플로우 시작
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    self.bot.logger.info("만료된 OAuth 토큰 갱신됨")
                except Exception as e:
                    self.bot.logger.error(f"토큰 갱신 오류: {e}")
                    creds = None
            else:
                # 새로운 OAuth 플로우 시작
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
                self.bot.logger.info("새로운 OAuth 인증 완료")

            # 다음 실행을 위해 자격증명 저장
            try:
                with open(TOKEN_FILE, 'wb') as token:
                    pickle.dump(creds, token)
                self.bot.logger.info("OAuth 토큰이 token.pickle에 저장됨")
            except Exception as e:
                self.bot.logger.error(f"토큰 저장 오류: {e}")

        return creds

    def _check_system_resources(self):
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()

            if cpu_percent > 80:
                return False, f"CPU 사용률이 너무 높음 ({cpu_percent:.1f}%)"
            if memory.percent > 85:
                return False, f"메모리 사용률이 너무 높음 ({memory.percent:.1f}%)"
            return True, "정상"
        except:
            return True, "정상"

    async def _upload_to_drive(self, folder_path, recording_id):
        """OAuth 2.0을 사용하여 폴더를 Google Drive에 업로드"""
        try:
            # OAuth 자격증명 가져오기
            creds = await asyncio.to_thread(self._get_oauth_credentials)
            drive_service = build('drive', 'v3', credentials=creds)

            # 대상 폴더 내에 폴더 생성
            folder_metadata = {
                'name': f'녹음_{recording_id}',
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [TARGET_FOLDER_ID]  # 기존 폴더 내에 배치
            }

            folder = drive_service.files().create(body=folder_metadata, fields='id').execute()
            folder_id = folder.get('id')

            self.bot.logger.info(f"대상 폴더 {TARGET_FOLDER_ID} 내에 폴더 {folder_id} 생성됨")

            # 모든 오디오 파일 업로드
            uploaded_files = []
            for file_name in os.listdir(folder_path):
                if file_name.endswith(('.wav', '.mp3', '.m4a')):
                    file_path = os.path.join(folder_path, file_name)

                    file_metadata = {
                        'name': file_name,
                        'parents': [folder_id]  # 새 하위 폴더에 배치
                    }

                    media = MediaFileUpload(file_path, resumable=True)

                    # 재시도 메커니즘으로 업로드
                    for attempt in range(5):
                        try:
                            file = drive_service.files().create(
                                body=file_metadata,
                                media_body=media,
                                fields='id'
                            ).execute()

                            uploaded_files.append(file.get('id'))
                            self.bot.logger.info(f"{file_name} 업로드됨 (ID: {file.get('id')})")
                            break

                        except Exception as e:
                            if attempt < 4:
                                self.bot.logger.warning(f"업로드 시도 {attempt + 1} 실패: {e}. 재시도 중...")
                                await asyncio.sleep(5)
                            else:
                                self.bot.logger.error(f"{file_name} 5번 시도 후 업로드 실패")
                                raise

            return folder_id, uploaded_files

        except Exception as e:
            self.bot.logger.error(f"Google Drive 업로드 오류: {e}")
            raise

    @discord.app_commands.command(name="녹음", description="음성 채널 녹음을 시작하거나 중지합니다")
    @discord.app_commands.describe(작업="녹음을 시작하거나 중지할지 선택하세요")
    @discord.app_commands.choices(작업=[
        discord.app_commands.Choice(name="시작", value="start"),
        discord.app_commands.Choice(name="중지", value="stop")
    ])
    async def record(self, interaction: discord.Interaction, 작업: str):
        if 작업 == "start":
            await self._start_recording(interaction)
        elif 작업 == "stop":
            await self._stop_recording(interaction)

    @discord.app_commands.command(name="녹음상태", description="현재 녹음 상태를 확인합니다")
    async def recording_status(self, interaction: discord.Interaction):
        if interaction.guild.id not in self.recordings:
            await interaction.response.send_message("🔹 이 서버에서 활성화된 녹음이 없습니다.")
            return

        recording = self.recordings[interaction.guild.id]
        duration = datetime.now() - recording['start_time']
        duration_str = str(duration).split('.')[0]

        embed = discord.Embed(
            title="🔴 녹음 진행 중",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.add_field(name="채널", value=recording['channel'].name, inline=True)
        embed.add_field(name="녹음 시간", value=duration_str, inline=True)
        embed.add_field(name="녹음 ID", value=recording['id'], inline=True)
        embed.add_field(name="연결된 사용자", value=len(recording['channel'].members), inline=True)

        try:
            cpu = psutil.cpu_percent()
            memory = psutil.virtual_memory().percent
            embed.add_field(name="시스템 부하", value=f"CPU: {cpu:.1f}% | RAM: {memory:.1f}%", inline=False)
        except:
            pass

        await interaction.response.send_message(embed=embed)

    async def _start_recording(self, interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("⛔ 음성 채널에 참여해야 합니다!", ephemeral=True)
            return

        if interaction.guild.id in self.recordings:
            await interaction.response.send_message("⛔ 이 서버에서 이미 녹음이 진행 중입니다!", ephemeral=True)
            return

        can_record, reason = self._check_system_resources()
        if not can_record:
            await interaction.response.send_message(f"⛔ 녹음을 시작할 수 없습니다: {reason}", ephemeral=True)
            return

        if len(self.recordings) >= self.max_concurrent_recordings:
            await interaction.response.send_message("⛔ 이 시스템에서 최대 녹음 수에 도달했습니다", ephemeral=True)
            return

        channel = interaction.user.voice.channel
        recording_id = str(int(datetime.now().timestamp()))
        recording_dir = os.path.join(self.recordings_path, recording_id)
        os.makedirs(recording_dir, exist_ok=True)

        await interaction.response.defer()

        try:
            self.bot.logger.info(f"녹음 시작 - 길드: {interaction.guild.name} ({interaction.guild.id})")
            self.bot.logger.info(f"채널: {channel.name} ({channel.id})")
            self.bot.logger.info(f"사용자: {interaction.user.display_name} ({interaction.user.id})")

            # 봇 권한 확인
            bot_member = interaction.guild.get_member(self.bot.user.id)
            if bot_member:
                permissions = channel.permissions_for(bot_member)
                self.bot.logger.info(f"봇 권한 - 연결: {permissions.connect}, 말하기: {permissions.speak}")
                if not permissions.connect or not permissions.speak:
                    await interaction.followup.send("⛔ 봇이 음성 채널에 필요한 권한이 부족합니다!",
                                                    ephemeral=True)
                    return

            # Node.js 녹음기 프로세스 시작
            env = os.environ.copy()
            env['DISCORD_BOT_TOKEN'] = self.bot.http.token

            # 녹음기 프로세스용 새 콘솔 윈도우 생성 (Windows) 또는 nohup 사용 (Unix)
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

            # 프로세스가 시작될 때까지 잠시 대기
            await asyncio.sleep(3)

            # 프로세스가 여전히 실행 중인지 확인
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                self.bot.logger.error(f"녹음기 프로세스 즉시 실패:")
                self.bot.logger.error(f"종료 코드: {process.returncode}")
                self.bot.logger.error(f"Stdout: {stdout}")
                self.bot.logger.error(f"Stderr: {stderr}")
                await interaction.followup.send(f"⛔ 녹음 프로세스 시작에 실패했습니다. 봇 로그를 확인해주세요.",
                                                ephemeral=True)
                return

            # 봇의 서버 닉네임을 녹음 중으로 변경
            try:
                bot_member = interaction.guild.get_member(self.bot.user.id)
                if bot_member:
                    original_nickname = bot_member.display_name
                    await bot_member.edit(nick="(음성 녹화중) 아날로그")
                    self.bot.logger.info(f"봇 닉네임을 '(음성 녹화중) 아날로그'로 변경")
                else:
                    original_nickname = None
            except discord.Forbidden:
                self.bot.logger.warning("봇 닉네임 변경 권한이 없습니다")
                original_nickname = None
            except Exception as e:
                self.bot.logger.error(f"봇 닉네임 변경 오류: {e}")
                original_nickname = None

            # 녹음 정보 저장 (원래 닉네임 포함)
            self.recordings[interaction.guild.id] = {
                'id': recording_id,
                'process': process,
                'channel': channel,
                'start_time': datetime.now(),
                'dir': recording_dir,
                'original_nickname': original_nickname
            }

            embed = discord.Embed(
                title="✅ 녹음 시작됨",
                description=f"{channel.name}에서 동기화된 트랙 녹음 중",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            embed.add_field(name="녹음 ID", value=f"`{recording_id}`", inline=True)
            embed.add_field(name="출력 디렉터리", value=f"`./recordings/{recording_id}/`", inline=False)
            embed.add_field(name="트랙 유형", value="사용자별 동기화된 개별 트랙 (userID_username.mp3)", inline=False)
            embed.add_field(
                name="🔄 동기화 정보",
                value="모든 트랙이 녹음 시작 시간부터 동일한 길이로 생성되며, 부재 시간은 침묵으로 채워집니다.",
                inline=False
            )
            embed.set_footer(text="/녹음 중지를 사용하여 녹음을 종료하세요")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            self.bot.logger.error(f"녹음 시작 오류: {e}", exc_info=True)

            # 오류 발생 시 봇 닉네임 복원
            try:
                bot_member = interaction.guild.get_member(self.bot.user.id)
                if bot_member:
                    await bot_member.edit(nick=None)
                    self.bot.logger.info("시작 오류 시 봇 닉네임 복원")
            except:
                pass

            if interaction.guild.id in self.recordings:
                del self.recordings[interaction.guild.id]
            await interaction.followup.send(f"⛔ 녹음 시작 실패: {str(e)[:100]}...", ephemeral=True)

    async def _stop_recording(self, interaction):
        if interaction.guild.id not in self.recordings:
            await interaction.response.send_message("⛔ 이 서버에서 진행 중인 녹음이 없습니다!", ephemeral=True)
            return

        recording = self.recordings[interaction.guild.id]
        await interaction.response.defer()

        try:
            self.bot.logger.info(f"길드 {interaction.guild.id}의 녹음 중지 중")

            # 녹음기 프로세스에 중지 명령 전송
            if recording['process'].poll() is None:
                self.bot.logger.info("녹음기에 중지 명령 전송 중")

                # 중지 명령 전송
                stop_env = dict(os.environ, DISCORD_BOT_TOKEN=self.bot.http.token)
                stop_process = subprocess.Popen([
                    'node', 'utils/voice_recorder.js', 'stop', str(interaction.guild.id)
                ], env=stop_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

                try:
                    # 동기화된 트랙 처리를 위해 타임아웃 조정
                    stdout, stderr = await asyncio.wait_for(
                        asyncio.create_task(asyncio.to_thread(stop_process.communicate)),
                        timeout=25.0  # 동기화 처리를 위해 25초로 증가
                    )
                    self.bot.logger.info(f"중지 명령 출력: {stdout}")
                    if stderr:
                        self.bot.logger.warning(f"중지 명령 stderr: {stderr}")
                except asyncio.TimeoutError:
                    stop_process.terminate()
                    self.bot.logger.warning("중지 명령 타임아웃")

            # 디렉터리 구조 디버깅
            self.bot.logger.info(f"녹음 디렉터리 확인: {recording['dir']}")
            if os.path.exists(recording['dir']):
                self.bot.logger.info(f"디렉터리 존재함. 내용: {os.listdir(recording['dir'])}")

                # 하위 디렉터리가 있는지 확인 (타임스탬프 폴더 때문에)
                for item in os.listdir(recording['dir']):
                    item_path = os.path.join(recording['dir'], item)
                    if os.path.isdir(item_path):
                        self.bot.logger.info(f"하위 디렉터리 발견: {item}")
                        self.bot.logger.info(f"하위 디렉터리 내용: {os.listdir(item_path)}")
                        # 실제 검색 경로를 하위 디렉터리로 업데이트
                        recording['dir'] = item_path
                        break
            else:
                self.bot.logger.warning(f"녹음 디렉터리가 존재하지 않음: {recording['dir']}")

            # 동기화된 트랙이 처리될 때까지 더 오래 대기
            max_wait_time = 60  # 1분으로 단축 (새 시스템은 더 빠름)
            check_interval = 5  # 5초마다 확인 (더 자주 확인)
            files_created = []

            for i in range(0, max_wait_time, check_interval):
                await asyncio.sleep(check_interval)

                if os.path.exists(recording['dir']):
                    all_files = os.listdir(recording['dir'])
                    # 새로운 파일명 시스템에 맞춰 user_ 파일 찾기
                    user_files = [f for f in all_files if
                                  f.endswith(('.wav', '.mp3', '.m4a')) and
                                  f.startswith('user_') and
                                  not f.startswith('stop')]

                    self.bot.logger.info(
                        f"확인 {i // check_interval + 1}: {len(user_files)}개의 동기화된 사용자 트랙 파일 발견")

                    for f in user_files:
                        file_path = os.path.join(recording['dir'], f)
                        if os.path.exists(file_path):
                            size = os.path.getsize(file_path)
                            self.bot.logger.info(f"  - {f}: {size} 바이트")

                    # 파일이 안정적인지 확인 (더 이상 증가하지 않음)
                    if user_files and i < max_wait_time - check_interval:
                        await asyncio.sleep(check_interval)
                        stable_files = []
                        for f in user_files:
                            file_path = os.path.join(recording['dir'], f)
                            if os.path.exists(file_path):
                                new_size = os.path.getsize(file_path)
                                if new_size > 1000:  # 1KB보다 큰 파일 허용
                                    stable_files.append(f)

                        if stable_files:
                            files_created = stable_files
                            break
                    elif user_files:
                        files_created = [f for f in user_files if
                                         os.path.getsize(os.path.join(recording['dir'], f)) > 1000]
                        break

            # 최종 종합 확인
            if not files_created and os.path.exists(recording['dir']):
                all_files = os.listdir(recording['dir'])
                self.bot.logger.info(f"최종 확인 - 디렉터리의 모든 파일: {all_files}")

                # 새로운 파일명 시스템에 맞춰 모든 오디오 파일 찾기
                for f in all_files:
                    if f.endswith(('.wav', '.mp3', '.m4a')) and not f.startswith('stop'):
                        file_path = os.path.join(recording['dir'], f)
                        size = os.path.getsize(file_path)
                        self.bot.logger.info(f"오디오 파일 발견: {f} ({size} 바이트)")
                        if size > 1000:  # 1KB보다 큰 파일 허용
                            files_created.append(f)

            duration = datetime.now() - recording['start_time']
            duration_str = str(duration).split('.')[0]

            # Google Drive에 업로드
            upload_embed = discord.Embed(
                title="📤 Google Drive에 업로드 중",
                description="동기화된 녹음 업로드 중입니다. 잠시만 기다려주세요...",
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )
            upload_embed.add_field(name="녹음 ID", value=f"`{recording['id']}`", inline=True)
            upload_embed.add_field(name="파일", value=f"{len(files_created)}개 동기화된 트랙 업로드 예정", inline=True)
            upload_embed.set_footer(text="큰 녹음의 경우 몇 분이 걸릴 수 있습니다")

            await interaction.followup.send(embed=upload_embed)

            # Google Drive에 업로드
            drive_folder_id = None
            try:
                drive_folder_id, uploaded_files = await self._upload_to_drive(recording['dir'], recording['id'])
                self.bot.logger.info(
                    f"Google Drive 폴더 {drive_folder_id}에 {len(uploaded_files)}개 파일 업로드 성공")
            except Exception as e:
                self.bot.logger.error(f"Google Drive 업로드 실패: {e}")
                drive_folder_id = None

            # 최종 상태 임베드 생성
            embed = discord.Embed(
                title="✅ 동기화된 녹음 중지됨",
                description="사용자별 동기화된 트랙 녹음이 완료되었습니다",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            embed.add_field(name="녹음 시간", value=duration_str, inline=True)
            embed.add_field(name="녹음 ID", value=f"`{recording['id']}`", inline=True)
            embed.add_field(name="트랙 파일", value=f"{len(files_created)}개 동기화된 사용자 트랙", inline=True)

            if drive_folder_id:
                embed.add_field(
                    name="🗂 Google Drive",
                    value=f"[동기화된 녹음 폴더 보기](https://drive.google.com/drive/folders/{drive_folder_id})",
                    inline=False
                )
                embed.color = discord.Color.green()
            else:
                embed.add_field(
                    name="⚠️ 업로드 상태",
                    value="Google Drive 업로드에 실패했습니다. 파일은 로컬에서 사용 가능합니다.",
                    inline=False
                )
                embed.color = discord.Color.red()

            if files_created:
                file_list = '\n'.join([f"• {f}" for f in files_created[:5]])
                if len(files_created) > 5:
                    file_list += f"\n• ... 그리고 {len(files_created) - 5}개 더"
                embed.add_field(name="동기화된 트랙 파일", value=f"```{file_list}```", inline=False)

                # 동기화된 사용자별 트랙에 대한 참고사항
                embed.add_field(
                    name="ℹ️ 동기화 정보",
                    value="각 파일은 한 사용자의 전체 세션 트랙을 포함하며, 녹음 시작부터 종료까지 완전히 동기화되어 부재 기간에는 무음이 포함됩니다. 모든 트랙의 길이가 동일합니다.",
                    inline=False
                )
            else:
                embed.add_field(name="상태", value="⛔ 동기화된 트랙 파일이 생성되지 않았습니다", inline=False)
                embed.color = discord.Color.red()

            await interaction.edit_original_response(embed=embed)

            # 활성 녹음에서 제거
            del self.recordings[interaction.guild.id]

            # 봇 닉네임을 원래대로 복원
            try:
                bot_member = interaction.guild.get_member(self.bot.user.id)
                if bot_member:
                    original_nickname = recording.get('original_nickname', None)
                    if original_nickname and original_nickname != "(음성 녹화중) 아날로그":
                        await bot_member.edit(nick=original_nickname)
                        self.bot.logger.info(f"봇 닉네임을 '{original_nickname}'로 복원")
                    else:
                        # 원래 닉네임이 없었거나 이미 녹화중이었다면 닉네임 제거
                        await bot_member.edit(nick=None)
                        self.bot.logger.info("봇 닉네임 제거됨")
            except discord.Forbidden:
                self.bot.logger.warning("봇 닉네임 복원 권한이 없습니다")
            except Exception as e:
                self.bot.logger.error(f"봇 닉네임 복원 오류: {e}")

        except Exception as e:
            self.bot.logger.error(f"녹음 중지 오류: {e}", exc_info=True)
            if interaction.guild.id in self.recordings:
                try:
                    self.recordings[interaction.guild.id]['process'].terminate()
                except:
                    pass

                # 오류 발생 시에도 봇 닉네임 복원 시도
                try:
                    recording = self.recordings[interaction.guild.id]
                    bot_member = interaction.guild.get_member(self.bot.user.id)
                    if bot_member:
                        original_nickname = recording.get('original_nickname', None)
                        if original_nickname and original_nickname != "(음성 녹화중) 아날로그":
                            await bot_member.edit(nick=original_nickname)
                        else:
                            await bot_member.edit(nick=None)
                        self.bot.logger.info("오류 시 봇 닉네임 복원 완료")
                except:
                    self.bot.logger.warning("오류 시 봇 닉네임 복원 실패")

                del self.recordings[interaction.guild.id]

            error_embed = discord.Embed(
                title="⚠ 녹음 오류",
                description="동기화된 녹음 처리 중 오류가 발생했습니다.",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            error_embed.add_field(name="오류", value=str(e)[:200], inline=False)

            await interaction.edit_original_response(embed=error_embed)


async def setup(bot):
    await bot.add_cog(Recording(bot))