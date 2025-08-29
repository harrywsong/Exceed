// voice_recorder.js — 긴 녹음(1-2시간+) 및 한국어 지원 버전
//
// 주요 수정사항:
// 1. 1-2시간 이상의 긴 녹음을 위한 메모리 및 성능 최적화
// 2. 모든 로그 및 메시지를 한국어로 변경
// 3. 중복 방지 파일명 시스템 구현
//    - 폴더: YYYY-MM-DD_HH-MM-SS 형식
//    - 파일: user_(discord_id)_(discord_username).mp3
// 4. 메모리 사용량 모니터링 및 가비지 컬렉션 최적화

const { Client, GatewayIntentBits, Partials, PermissionsBitField } = require('discord.js');
const {
  joinVoiceChannel,
  VoiceConnectionStatus,
  EndBehaviorType,
  entersState,
  createAudioPlayer,
  createAudioResource,
  NoSubscriberBehavior,
  StreamType,
} = require('@discordjs/voice');
const prism = require('prism-media');
const { Readable, PassThrough, Transform } = require('stream');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

// --- 상수 정의 ---
const STATE_FILE = path.join(process.cwd(), 'recording_state.json');
const STOP_FILENAME = 'stop.flag';
const SAMPLE_RATE = 48000;
const CHANNELS = 2;
const FRAME_SIZE = 960; // 48kHz에서 20ms
const BYTES_PER_SAMPLE = 2; // 16-bit
const BYTES_PER_FRAME = FRAME_SIZE * CHANNELS * BYTES_PER_SAMPLE;
const SILENCE_INTERVAL_MS = 20; // 정확한 20ms 간격

// 긴 녹음을 위한 성능 설정
const MEMORY_CHECK_INTERVAL = 30000; // 30초마다 메모리 확인
const MAX_MEMORY_USAGE_MB = 1000; // 최대 메모리 사용량 (MB)
const GC_INTERVAL = 120000; // 2분마다 가비지 컬렉션

// 한국어 사용자명 정리를 위한 함수
function sanitizeKoreanUsername(username) {
  // 한국어, 영어, 숫자, 일부 특수문자만 허용
  return username
    .replace(/[^\w가-힣ㄱ-ㅎㅏ-ㅣ\-_]/g, '') // 허용되지 않은 문자 제거
    .substring(0, 32); // 최대 32자로 제한
}

// 날짜/시간 기반 폴더명 생성
function createTimestampFolderName() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  const hours = String(now.getHours()).padStart(2, '0');
  const minutes = String(now.getMinutes()).padStart(2, '0');
  const seconds = String(now.getSeconds()).padStart(2, '0');

  return `${year}-${month}-${day}_${hours}-${minutes}-${seconds}`;
}

// 단순한 침묵 유지용 클래스
class Silence extends Readable {
  _read() {
    this.push(Buffer.from([0xf8, 0xff, 0xfe]));
  }
}

// 최적화된 침묵 생성기 (긴 녹음용)
class OptimizedSilenceGenerator extends Readable {
  constructor(options = {}) {
    super(options);
    this.sampleRate = SAMPLE_RATE;
    this.channels = CHANNELS;
    this.destroyed = false;

    // 20ms에 필요한 정확한 샘플 수 계산
    this.samplesPerChunk = Math.floor(this.sampleRate * SILENCE_INTERVAL_MS / 1000);
    this.chunkSize = this.samplesPerChunk * this.channels * BYTES_PER_SAMPLE;

    console.log(`[침묵생성] ${SILENCE_INTERVAL_MS}ms마다 ${this.chunkSize}바이트 생성`);

    // 메모리 효율적인 침묵 버퍼 재사용
    this.silenceBuffer = Buffer.alloc(this.chunkSize, 0);

    // 정밀한 타이머 사용
    this.timer = setInterval(() => this._generateSilenceChunk(), SILENCE_INTERVAL_MS);
  }

  _generateSilenceChunk() {
    if (this.destroyed) return;

    // 미리 생성된 침묵 버퍼 재사용 (메모리 효율성)
    this.push(Buffer.from(this.silenceBuffer));
  }

  _read() {
    // 데이터는 타이머에서 푸시됨
  }

  destroy() {
    this.destroyed = true;
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.silenceBuffer = null;
    super.destroy();
  }
}

// 메모리 최적화된 오디오 믹서 (긴 녹음용)
class MemoryOptimizedAudioMixer extends PassThrough {
  constructor(options = {}) {
    super({
      ...options,
      highWaterMark: 16384, // 버퍼 크기 제한 (긴 녹음용)
    });
    this.isReceivingVoice = false;
    this.silenceGenerator = null;
    this.currentVoiceStream = null;
    this.destroyed = false;

    this._startSilenceMode();
  }

  _startSilenceMode() {
    if (this.destroyed) return;

    console.log('[믹서] 침묵 모드 시작');
    this.isReceivingVoice = false;

    if (this.silenceGenerator) {
      this.silenceGenerator.destroy();
    }

    this.silenceGenerator = new OptimizedSilenceGenerator();

    this.silenceGenerator.on('data', (chunk) => {
      if (!this.isReceivingVoice && !this.destroyed) {
        this.write(chunk);
      }
    });

    this.silenceGenerator.on('error', (err) => {
      console.warn('[믹서] 침묵 생성기 오류:', err.message);
    });
  }

  switchToVoice(voiceStream) {
    if (this.destroyed) return;

    console.log('[믹서] 음성 모드로 전환');
    this.isReceivingVoice = true;

    // 침묵 생성 중지
    if (this.silenceGenerator) {
      this.silenceGenerator.destroy();
      this.silenceGenerator = null;
    }

    this.currentVoiceStream = voiceStream;

    voiceStream.on('data', (chunk) => {
      if (this.isReceivingVoice && !this.destroyed) {
        this.write(chunk);
      }
    });

    voiceStream.on('end', () => {
      console.log('[믹서] 음성 스트림 종료, 침묵 모드로 복귀');
      this._startSilenceMode();
    });

    voiceStream.on('error', (err) => {
      console.warn('[믹서] 음성 스트림 오류:', err.message);
      this._startSilenceMode();
    });
  }

  destroy() {
    this.destroyed = true;

    if (this.silenceGenerator) {
      this.silenceGenerator.destroy();
      this.silenceGenerator = null;
    }

    if (this.currentVoiceStream) {
      try { this.currentVoiceStream.destroy(); } catch (_) {}
      this.currentVoiceStream = null;
    }

    super.destroy();
  }
}

class UserTrackManager {
  constructor(userId, username, outputDir, format, bitrate, recordingStartTime) {
    this.userId = userId;
    this.username = sanitizeKoreanUsername(username);
    this.outputDir = outputDir;
    this.format = format;
    this.bitrate = bitrate;
    this.recordingStartTime = recordingStartTime;

    this.isPresent = false;
    this.isReceivingAudio = false;
    this.joinTime = null;
    this.leaveTime = null;

    // 메모리 최적화된 오디오 파이프라인
    this.audioMixer = new MemoryOptimizedAudioMixer();
    this.currentOpusStream = null;
    this.currentDecoder = null;
    this.ffmpegProcess = null;

    // 새로운 파일명 시스템: user_(discord_id)_(discord_username).mp3
    this.filename = path.join(outputDir, `user_${userId}_${this.username}.${format}`);

    // 중복 방지 체크
    let counter = 1;
    while (fs.existsSync(this.filename)) {
      this.filename = path.join(outputDir, `user_${userId}_${this.username}_${counter}.${format}`);
      counter++;
    }

    console.log(`[트랙] 사용자 ${this.username}(${userId})의 파일: ${path.basename(this.filename)}`);

    this._setupFFmpeg();
    this._setupMemoryMonitoring();
  }

  _setupFFmpeg() {
    // 긴 녹음을 위한 최적화된 FFmpeg 설정
    const args = [
      '-f', 's16le',
      '-ar', SAMPLE_RATE.toString(),
      '-ac', CHANNELS.toString(),
      '-i', 'pipe:0',
      '-threads', '2', // 긴 녹음을 위해 스레드 수 증가
      '-buffer_size', '65536', // 더 큰 버퍼 크기 (긴 녹음용)
      '-max_delay', '2000000', // 2초 최대 지연 허용
      '-fflags', '+genpts+flush_packets', // 패킷 플러시 추가
      '-avoid_negative_ts', 'make_zero',
      '-flush_packets', '1', // 실시간 플러시
    ];

    if (this.format === 'mp3') {
      args.push(
        '-acodec', 'libmp3lame',
        '-b:a', this.bitrate,
        '-q:a', '2', // 고품질 설정
        '-joint_stereo', '1', // 메모리 효율성
        '-f', 'mp3'
      );
    } else {
      args.push(
        '-acodec', 'pcm_s16le',
        '-f', 'wav'
      );
    }

    args.push('-y', this.filename);

    console.log(`[트랙] 사용자 ${this.username} FFmpeg 시작: ${args.slice(-3).join(' ')}`);

    this.ffmpegProcess = spawn('ffmpeg', args, {
      stdio: ['pipe', 'pipe', 'pipe']
    });

    // 향상된 오류 처리
    this.ffmpegProcess.stderr.on('data', (data) => {
      const errorMsg = data.toString();
      if (errorMsg.includes('Error') || errorMsg.includes('failed') || errorMsg.includes('Invalid')) {
        console.error(`[FFmpeg] 사용자 ${this.username} 오류:`, errorMsg.trim());
      }
    });

    this.ffmpegProcess.on('error', (err) => {
      console.error(`[FFmpeg] 사용자 ${this.username} 프로세스 오류:`, err.message);
    });

    this.ffmpegProcess.on('close', (code) => {
      console.log(`[FFmpeg] 사용자 ${this.username} 프로세스 종료 (코드: ${code})`);
      this._validateOutputFile();
    });

    this.ffmpegProcess.stdin.on('error', (err) => {
      console.warn(`[FFmpeg] 사용자 ${this.username} stdin 오류:`, err.message);
    });

    // 오디오 믹서를 FFmpeg에 연결
    this.audioMixer.pipe(this.ffmpegProcess.stdin);
  }

  _setupMemoryMonitoring() {
    // 긴 녹음을 위한 메모리 모니터링
    this.memoryCheckInterval = setInterval(() => {
      const memoryUsage = process.memoryUsage();
      const memoryMB = Math.round(memoryUsage.heapUsed / 1024 / 1024);

      if (memoryMB > MAX_MEMORY_USAGE_MB) {
        console.warn(`[메모리] 사용자 ${this.username} 높은 메모리 사용량: ${memoryMB}MB`);

        // 강제 가비지 컬렉션 시도
        if (global.gc) {
          global.gc();
          console.log(`[메모리] 가비지 컬렉션 실행됨`);
        }
      }
    }, MEMORY_CHECK_INTERVAL);
  }

  userJoined(joinTime = Date.now()) {
    const joinDate = new Date(joinTime).toLocaleString('ko-KR');
    console.log(`[트랙] 사용자 ${this.username}가 ${joinDate}에 입장`);
    this.isPresent = true;
    this.joinTime = joinTime;
  }

  userLeft(leaveTime = Date.now()) {
    const leaveDate = new Date(leaveTime).toLocaleString('ko-KR');
    console.log(`[트랙] 사용자 ${this.username}가 ${leaveDate}에 퇴장`);
    this.isPresent = false;
    this.leaveTime = leaveTime;
    this._stopCurrentAudioStream();
  }

  startReceivingAudio(opusStream) {
    console.log(`[트랙] 사용자 ${this.username} 오디오 수신 시작`);

    this._stopCurrentAudioStream();

    this.isReceivingAudio = true;
    this.currentOpusStream = opusStream;

    // Opus 디코더 설정 (긴 녹음 최적화)
    this.currentDecoder = new prism.opus.Decoder({
      rate: SAMPLE_RATE,
      channels: CHANNELS,
      frameSize: FRAME_SIZE
    });

    this.currentDecoder.on('error', (err) => {
      console.error(`[디코더] 사용자 ${this.username} 오류:`, err.message);
      this._stopCurrentAudioStream();
    });

    opusStream.on('end', () => {
      console.log(`[Opus] 사용자 ${this.username} 스트림 종료`);
      this._stopCurrentAudioStream();
    });

    opusStream.on('error', (err) => {
      console.error(`[Opus] 사용자 ${this.username} 스트림 오류:`, err.message);
      this._stopCurrentAudioStream();
    });

    // 스트림 파이프라인: Opus -> 디코더 -> 믹서
    opusStream.pipe(this.currentDecoder);
    this.audioMixer.switchToVoice(this.currentDecoder);
  }

  stopReceivingAudio() {
    console.log(`[트랙] 사용자 ${this.username} 오디오 수신 중지`);
    this._stopCurrentAudioStream();
  }

  _stopCurrentAudioStream() {
    this.isReceivingAudio = false;

    if (this.currentOpusStream) {
      try {
        this.currentOpusStream.destroy();
      } catch (_) {}
      this.currentOpusStream = null;
    }

    if (this.currentDecoder) {
      try {
        this.currentDecoder.end();
      } catch (_) {}
      this.currentDecoder = null;
    }
  }

  cleanup() {
    console.log(`[트랙] 사용자 ${this.username} 정리 중`);

    // 메모리 모니터링 중지
    if (this.memoryCheckInterval) {
      clearInterval(this.memoryCheckInterval);
    }

    this._stopCurrentAudioStream();

    if (this.audioMixer) {
      try {
        this.audioMixer.end();
        this.audioMixer.destroy();
      } catch (_) {}
    }

    // FFmpeg 정리 (긴 녹음을 위해 더 긴 시간 허용)
    if (this.ffmpegProcess && !this.ffmpegProcess.killed) {
      setTimeout(() => {
        try {
          if (!this.ffmpegProcess.killed) {
            this.ffmpegProcess.stdin.end();
          }
        } catch (_) {}
      }, 3000);

      setTimeout(() => {
        try {
          if (!this.ffmpegProcess.killed) {
            this.ffmpegProcess.kill('SIGTERM');
          }
        } catch (_) {}
      }, 12000); // 긴 녹음을 위해 12초로 증가
    }
  }

  _validateOutputFile() {
    try {
      if (fs.existsSync(this.filename)) {
        const stats = fs.statSync(this.filename);
        const size = stats.size;
        const sizeMB = (size / (1024 * 1024)).toFixed(2);
        const durationEstimate = size / (SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE);
        const durationMinutes = (durationEstimate / 60).toFixed(1);

        console.log(`[트랙] 사용자 ${this.username} 최종 파일: ${path.basename(this.filename)}`);
        console.log(`[트랙] 크기: ${sizeMB}MB, 예상 길이: ${durationMinutes}분`);

        if (size < 10000) { // 10KB 미만
          console.warn(`[트랙] 사용자 ${this.username} 파일이 의심스럽게 작음`);
        } else if (durationEstimate > 7200) { // 2시간 초과
          console.warn(`[트랙] 사용자 ${this.username} 파일이 의심스럽게 큼 - 타이밍 문제 가능성`);
        } else {
          console.log(`[트랙] 사용자 ${this.username} 파일이 정상적으로 보임`);
        }
      } else {
        console.warn(`[트랙] 사용자 ${this.username} 출력 파일 누락: ${this.filename}`);
      }
    } catch (e) {
      console.error(`[트랙] 사용자 ${this.username} 출력 파일 검증 오류:`, e.message);
    }
  }
}

class VoiceRecorder {
  constructor(token) {
    this.client = new Client({
      intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates],
      partials: [Partials.GuildMember, Partials.Channel],
    });
    this.token = token;
    this.activeRecording = null;
    this.isReady = false;
    this.shouldExit = false;

    // 긴 녹음을 위한 가비지 컬렉션 스케줄링
    this._setupGarbageCollection();
  }

  _setupGarbageCollection() {
    if (global.gc) {
      setInterval(() => {
        const memoryBefore = process.memoryUsage().heapUsed;
        global.gc();
        const memoryAfter = process.memoryUsage().heapUsed;
        const freed = Math.round((memoryBefore - memoryAfter) / 1024 / 1024);

        if (freed > 10) {
          console.log(`[GC] ${freed}MB 메모리 해제됨`);
        }
      }, GC_INTERVAL);
    }
  }

  checkFFmpeg() {
    return new Promise((resolve) => {
      const ffmpeg = spawn('ffmpeg', ['-version'], { stdio: 'ignore' });
      ffmpeg.on('close', (code) => resolve(code === 0));
      ffmpeg.on('error', () => resolve(false));
    });
  }

  loadState() {
    try {
      if (fs.existsSync(STATE_FILE)) {
        return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
      }
    } catch (e) {
      console.warn('[상태] 로드 오류:', e.message);
    }
    return null;
  }

  saveState(recording) {
    try {
      if (recording) {
        const state = {
          guildId: recording.guildId,
          channelId: recording.channelId,
          outputDir: recording.outputDir,
          startTime: recording.startTime,
          pid: process.pid,
          format: recording.format,
          bitrate: recording.bitrate,
        };
        fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
      } else if (fs.existsSync(STATE_FILE)) {
        fs.unlinkSync(STATE_FILE);
      }
    } catch (e) {
      console.warn('[상태] 저장 오류:', e.message);
    }
  }

  checkExistingRecording() {
    const state = this.loadState();
    if (!state) return null;
    try {
      process.kill(state.pid, 0);
      return state;
    } catch (_) {
      this.saveState(null);
      return null;
    }
  }

  async init() {
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('30초 후 로그인 타임아웃')), 30000);

      const onReady = () => {
        clearTimeout(timeout);
        this.isReady = true;
        console.log(`[녹음기] ${this.client.user.tag}로 로그인됨`);
        resolve();
      };

      this.client.once('clientReady', onReady);
      this.client.once('ready', onReady);

      this.client.on('error', (err) => {
        console.error('[디스코드] 클라이언트 오류:', err);
        if (!this.isReady) {
          clearTimeout(timeout);
          reject(err);
        }
      });

      this.client.login(this.token).catch((err) => {
        clearTimeout(timeout);
        reject(err);
      });
    });
  }

  async startRecording(guildId, channelId, baseOutputDir, opts = {}) {
    const format = (opts.format || 'mp3').toLowerCase();
    const bitrate = opts.bitrate || '192k';

    if (!this.isReady) {
      console.error('[시작] 클라이언트가 준비되지 않음');
      return false;
    }

    if (!(await this.checkFFmpeg())) {
      console.error('[시작] PATH에 FFmpeg를 찾을 수 없음');
      return false;
    }

    const existing = this.checkExistingRecording();
    if (existing) {
      console.warn('[시작] 이미 실행 중인 녹음:', existing);
      return false;
    }

    const guild = this.client.guilds.cache.get(guildId);
    if (!guild) {
      console.error(`[시작] 길드를 찾을 수 없음: ${guildId}`);
      return false;
    }

    const channel = guild.channels.cache.get(channelId);
    if (!channel || channel.type !== 2) {
      console.error(`[시작] 음성 채널을 찾을 수 없거나 유효하지 않음: ${channelId}`);
      return false;
    }

    const me = guild.members.me;
    const perms = channel.permissionsFor(me);
    if (!perms?.has(PermissionsBitField.Flags.Connect) || !perms?.has(PermissionsBitField.Flags.ViewChannel)) {
      console.error('[시작] 필요한 권한이 부족함');
      return false;
    }

    // 새로운 폴더명 시스템 사용
    const timestampFolder = createTimestampFolderName();
    const outputDir = path.join(baseOutputDir, timestampFolder);

    console.log(`[음성] 채널 입장 중... (폴더: ${timestampFolder})`);
    const connection = joinVoiceChannel({
      channelId: channelId,
      guildId: guildId,
      adapterCreator: guild.voiceAdapterCreator,
      selfDeaf: false,
      selfMute: false,
    });

    try {
      await entersState(connection, VoiceConnectionStatus.Ready, 20000);
      console.log('[음성] 연결 준비 완료');
    } catch (e) {
      console.error('[음성] 준비 상태 진입 실패:', e.message);
      try { connection.destroy(); } catch (_) {}
      return false;
    }

    // 연결 유지를 위한 침묵
    const player = createAudioPlayer({ behaviors: { noSubscriber: NoSubscriberBehavior.Play } });
    const silence = new Silence();
    const resource = createAudioResource(silence, { inputType: StreamType.Opus });
    connection.subscribe(player);
    player.play(resource);

    const recordingStartTime = Date.now();
    const rec = {
      connection,
      outputDir,
      startTime: recordingStartTime,
      userTracks: new Map(),
      guildId,
      channelId,
      player,
      format,
      bitrate,
      stopFlagPath: path.join(outputDir, STOP_FILENAME),
      recordingStartTime,
      timestampFolder,
    };

    this.activeRecording = rec;
    fs.mkdirSync(outputDir, { recursive: true });
    this.saveState(rec);

    this._setupReceiver(rec);
    this._setupVoiceStateTracking(rec);
    this._monitorStopFlag(rec);

    // 기존 사용자들을 위한 트랙 초기화
    for (const member of channel.members.values()) {
      if (!member.user.bot) {
        await this._initializeUserTrack(rec, member.user.id, member.user.username);
      }
    }

    // 연결 해제 처리
    connection.on(VoiceConnectionStatus.Disconnected, async () => {
      console.log('[음성] 연결 해제됨, 복구 시도 중...');
      try {
        await Promise.race([
          entersState(connection, VoiceConnectionStatus.Signalling, 5000),
          entersState(connection, VoiceConnectionStatus.Connecting, 5000),
        ]);
        console.log('[음성] 재연결됨');
      } catch (err) {
        console.error('[음성] 재연결 불가:', err.message);
        try { connection.destroy(); } catch (_) {}
        await this._stopCurrentRecording();
      }
    });

    console.log(`[시작] 녹음 설정 완료 (폴더: ${timestampFolder})`);
    return true;
  }

  async _initializeUserTrack(rec, userId, username) {
    if (!rec.userTracks.has(userId)) {
      console.log(`[녹음기] 사용자 ${username}(${userId}) 트랙 초기화 중`);
      const trackManager = new UserTrackManager(
        userId,
        username,
        rec.outputDir,
        rec.format,
        rec.bitrate,
        rec.recordingStartTime
      );
      rec.userTracks.set(userId, trackManager);
      trackManager.userJoined(rec.recordingStartTime);
    }
  }

  _setupVoiceStateTracking(rec) {
    this.client.on('voiceStateUpdate', async (oldState, newState) => {
      if (oldState.channelId !== rec.channelId && newState.channelId !== rec.channelId) {
        return;
      }

      const userId = newState.member.user.id;
      const username = newState.member.user.username;
      if (newState.member.user.bot) return;

      const now = Date.now();

      if (!oldState.channelId && newState.channelId === rec.channelId) {
        console.log(`[음성] 사용자 ${username}(${userId})가 녹음 채널에 입장`);
        await this._initializeUserTrack(rec, userId, username);
        const track = rec.userTracks.get(userId);
        if (track && !track.isPresent) {
          track.userJoined(now);
        }
      }
      else if (oldState.channelId === rec.channelId && !newState.channelId) {
        console.log(`[음성] 사용자 ${username}(${userId})가 녹음 채널에서 퇴장`);
        const track = rec.userTracks.get(userId);
        if (track && track.isPresent) {
          track.userLeft(now);
        }
      }
    });
  }

  _setupReceiver(rec) {
    const receiver = rec.connection.receiver;
    console.log('[수신기] 음성 수신 리스너 설정 중');

    receiver.speaking.on('start', async (userId) => {
      const user = this.client.users.cache.get(userId);
      if (user?.bot) return;

      console.log(`[수신기] 사용자 ${user?.username}(${userId}) 말하기 시작`);

      if (!rec.userTracks.has(userId)) {
        await this._initializeUserTrack(rec, userId, user?.username || `Unknown_${userId}`);
      }

      const trackManager = rec.userTracks.get(userId);
      if (!trackManager) {
        console.warn(`[수신기] 사용자 ${userId}의 트랙 매니저 없음`);
        return;
      }

      try {
        const opusStream = receiver.subscribe(userId, {
          end: { behavior: EndBehaviorType.AfterSilence, duration: 1000 },
        });

        trackManager.startReceivingAudio(opusStream);
      } catch (e) {
        console.error(`[수신기] 사용자 ${userId} 구독 실패:`, e.message);
      }
    });

    receiver.speaking.on('end', (userId) => {
      const user = this.client.users.cache.get(userId);
      console.log(`[수신기] 사용자 ${user?.username}(${userId}) 말하기 중지`);
      const trackManager = rec.userTracks.get(userId);
      if (trackManager) {
        trackManager.stopReceivingAudio();
      }
    });
  }

  _monitorStopFlag(rec) {
    const interval = setInterval(async () => {
      if (this.shouldExit) {
        clearInterval(interval);
        return;
      }
      try {
        if (fs.existsSync(rec.stopFlagPath)) {
          console.log('[중지] 중지 플래그 감지됨');
          clearInterval(interval);
          await this._stopCurrentRecording();
        }
      } catch (e) {
        console.warn('[중지] 모니터 오류:', e.message);
      }
    }, 1000);
  }

  async stopRecording(guildId) {
    if (this.activeRecording && this.activeRecording.guildId === guildId) {
      console.log('[중지] 현재 녹음 중지 중...');
      return await this._stopCurrentRecording();
    }

    const state = this.loadState();
    if (!state || state.guildId !== guildId) {
      console.log('[중지] 이 길드에 대한 활성 녹음 상태 없음');
      return false;
    }

    const stopPath = path.join(state.outputDir, STOP_FILENAME);
    try {
      fs.writeFileSync(stopPath, '');
      console.log(`[중지] 중지 플래그 작성됨: ${stopPath}`);
    } catch (e) {
      console.warn('[중지] 중지 플래그 작성 불가:', e.message);
    }

    // 다른 프로세스가 중지하기를 기다림
    for (let i = 0; i < 30; i++) { // 긴 녹음을 위해 30초로 증가
      await new Promise((r) => setTimeout(r, 1000));
      const s = this.loadState();
      if (!s || s.guildId !== guildId) {
        console.log('[중지] 다른 프로세스가 성공적으로 중지됨');
        return true;
      }
    }

    console.warn('[중지] 다른 프로세스가 중지되지 않음, 강제 종료 시도');
    try {
      process.kill(state.pid);
      this.saveState(null);
      return true;
    } catch (e) {
      console.warn('[중지] 강제 종료 실패:', e.message);
      return false;
    }
  }

  async _stopCurrentRecording() {
    if (!this.activeRecording) return false;

    const rec = this.activeRecording;
    console.log('[정리] 모든 사용자 트랙 중지 중...');

    // 모든 사용자 트랙 중지
    const trackPromises = [];
    for (const [userId, trackManager] of rec.userTracks) {
      console.log(`[정리] 사용자 ${trackManager.username}(${userId}) 트랙 정리 중`);
      trackPromises.push(
        new Promise((resolve) => {
          trackManager.cleanup();
          // 각 트랙마다 개별적으로 시간 허용
          setTimeout(resolve, 3000);
        })
      );
    }

    // 모든 트랙 정리 완료 대기
    await Promise.all(trackPromises);

    // 긴 녹음을 위해 인코더 완료까지 더 긴 시간 대기
    console.log('[정리] 인코더 완료까지 8초 대기...');
    await new Promise((r) => setTimeout(r, 8000));

    if (rec.player) {
      try { rec.player.stop(); } catch (_) {}
    }
    if (rec.connection) {
      try { rec.connection.destroy(); } catch (_) {}
    }

    this.activeRecording = null;
    this.saveState(null);

    try {
      if (fs.existsSync(rec.stopFlagPath)) {
        fs.unlinkSync(rec.stopFlagPath);
      }
    } catch (_) {}

    // 최종 통계 출력
    const fileCount = rec.userTracks.size;
    const endTime = new Date();
    const startTime = new Date(rec.recordingStartTime);
    const duration = Math.round((endTime - startTime) / 1000 / 60 * 10) / 10; // 분 단위

    console.log(`[정리] 녹음 성공적으로 중지됨`);
    console.log(`[정리] 폴더: ${rec.timestampFolder}`);
    console.log(`[정리] 파일 수: ${fileCount}개`);
    console.log(`[정리] 총 길이: ${duration}분`);

    return true;
  }

  async cleanup() {
    console.log('[정리] 전역 정리 중...');
    if (this.activeRecording) {
      await this._stopCurrentRecording();
    }
    if (this.client && this.client.isReady()) {
      try {
        await this.client.destroy();
      } catch (e) {
        console.warn('[정리] 클라이언트 종료 오류:', e.message);
      }
    }
    console.log('[정리] 정리 완료');
  }
}

// CLI 인터페이스
if (require.main === module) {
  const [,, action, ...rest] = process.argv;

  if (!process.env.DISCORD_BOT_TOKEN) {
    console.error('DISCORD_BOT_TOKEN 환경 변수가 설정되지 않음');
    process.exit(1);
  }

  const parseFlags = (args) => {
    const out = { _: [] };
    for (let i = 0; i < args.length; i++) {
      const a = args[i];
      if (a === '--format') { out.format = (args[++i] || 'mp3'); continue; }
      if (a === '--bitrate') { out.bitrate = (args[++i] || '192k'); continue; }
      out._.push(a);
    }
    return out;
  };

  const recorder = new VoiceRecorder(process.env.DISCORD_BOT_TOKEN);

  const shutdown = async (signal) => {
    console.log(`\n[프로세스] ${signal} 신호 수신, 종료 중...`);
    recorder.shouldExit = true;
    await recorder.cleanup();
    process.exit(0);
  };

  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));

  process.on('uncaughtException', async (err) => {
    console.error('[예외]', err);
    await recorder.cleanup();
    process.exit(1);
  });

  process.on('unhandledRejection', async (reason) => {
    console.error('[거부]', reason);
    await recorder.cleanup();
    process.exit(1);
  });

  recorder.init().then(async () => {
    switch (action) {
      case 'start': {
        const flags = parseFlags(rest);
        const [guildId, channelId, outputDir] = flags._;
        if (!guildId || !channelId || !outputDir) {
          console.error('사용법: node voice_recorder.js start <guildId> <channelId> <outputDir> [--format mp3|wav] [--bitrate 192k]');
          console.error('예시: node voice_recorder.js start 123456789 987654321 ./recordings --format mp3 --bitrate 192k');
          process.exit(1);
        }

        if (!fs.existsSync(outputDir)) {
          fs.mkdirSync(outputDir, { recursive: true });
        }

        console.log('[CLI] 녹음 시작 중...');
        console.log(`[CLI] 서버 ID: ${guildId}`);
        console.log(`[CLI] 채널 ID: ${channelId}`);
        console.log(`[CLI] 출력 디렉터리: ${outputDir}`);
        console.log(`[CLI] 형식: ${flags.format || 'mp3'}`);
        console.log(`[CLI] 비트레이트: ${flags.bitrate || '192k'}`);

        const ok = await recorder.startRecording(guildId, channelId, outputDir, {
          format: flags.format,
          bitrate: flags.bitrate
        });

        if (ok) {
          console.log('[CLI] 녹음이 성공적으로 시작됨');
          console.log('[CLI] 프로세스를 종료하려면 Ctrl+C를 누르거나 stop 명령을 사용하세요');
          // 프로세스 유지
          setInterval(() => {}, 1 << 30);
        } else {
          console.error('[CLI] 녹음 시작 실패');
          await recorder.cleanup();
          process.exit(1);
        }
        break;
      }
      case 'stop': {
        const [guildId] = rest;
        if (!guildId) {
          console.error('사용법: node voice_recorder.js stop <guildId>');
          process.exit(1);
        }

        console.log(`[CLI] 서버 ${guildId}의 녹음 중지 중...`);
        const ok = await recorder.stopRecording(guildId);
        console.log(ok ? '[CLI] 녹음이 성공적으로 중지됨' : '[CLI] 중지 실패 또는 중지할 녹음이 없음');
        await recorder.cleanup();
        process.exit(0);
      }
      default:
        console.error('사용법: node voice_recorder.js <start|stop> ...');
        console.error('  start <guildId> <channelId> <outputDir> [--format mp3|wav] [--bitrate 192k]');
        console.error('  stop <guildId>');
        console.error('');
        console.error('예시:');
        console.error('  node voice_recorder.js start 123456789 987654321 ./recordings');
        console.error('  node voice_recorder.js stop 123456789');
        await recorder.cleanup();
        process.exit(1);
    }
  }).catch(async (err) => {
    console.error('[초기화] 초기화 실패:', err);
    await recorder.cleanup();
    process.exit(1);
  });
}

module.exports = VoiceRecorder;