// voice_recorder.js — Fixed version with smooth continuous audio
//
// Fixes:
// - Proper continuous audio streaming without gaps
// - Better synchronization and buffering
// - Smoother silence generation using consistent timing
// - Improved FFmpeg parameters for stable output
//
// Requirements:
//   npm i discord.js @discordjs/voice prism-media
//   FFmpeg installed and on PATH
//   DISCORD_BOT_TOKEN in env

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

// --- Constants ---
const STATE_FILE = path.join(process.cwd(), 'recording_state.json');
const STOP_FILENAME = 'stop.flag';
const SAMPLE_RATE = 48000;
const CHANNELS = 2;
const FRAME_SIZE = 960; // 20ms at 48kHz
const BYTES_PER_SAMPLE = 2; // 16-bit
const BYTES_PER_FRAME = FRAME_SIZE * CHANNELS * BYTES_PER_SAMPLE;
const CHUNK_DURATION_MS = 20; // 20ms chunks for smooth playback

// Keep alive with a single Opus silence frame
class Silence extends Readable {
  _read() {
    this.push(Buffer.from([0xf8, 0xff, 0xfe]));
  }
}

// High-precision silence generator that maintains perfect timing
class PrecisionSilenceGenerator extends Readable {
  constructor(options = {}) {
    super(options);
    this.sampleRate = SAMPLE_RATE;
    this.channels = CHANNELS;
    this.bytesPerFrame = BYTES_PER_FRAME;
    this.startTime = process.hrtime.bigint();
    this.frameCount = 0;
    this.destroyed = false;

    // Use high-precision timer for consistent 20ms intervals
    this.timer = setInterval(() => this._generateFrame(), CHUNK_DURATION_MS);
  }

  _generateFrame() {
    if (this.destroyed) return;

    const silenceFrame = Buffer.alloc(this.bytesPerFrame);
    this.push(silenceFrame);
    this.frameCount++;
  }

  _read() {
    // Data is pushed by the timer
  }

  destroy() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.destroyed = true;
    super.destroy();
  }
}

// Audio mixer that seamlessly switches between silence and voice
class SeamlessAudioMixer extends Transform {
  constructor(options = {}) {
    super(options);
    this.isReceivingVoice = false;
    this.silenceGenerator = new PrecisionSilenceGenerator();
    this.voiceStream = null;

    // Start with silence
    this._startSilence();
  }

  _startSilence() {
    if (!this.silenceGenerator || this.silenceGenerator.destroyed) {
      this.silenceGenerator = new PrecisionSilenceGenerator();
    }

    this.silenceGenerator.on('data', (chunk) => {
      if (!this.isReceivingVoice && !this.destroyed) {
        this.push(chunk);
      }
    });
  }

  switchToVoice(voiceStream) {
    this.isReceivingVoice = true;
    this.voiceStream = voiceStream;

    voiceStream.on('data', (chunk) => {
      if (this.isReceivingVoice && !this.destroyed) {
        this.push(chunk);
      }
    });

    voiceStream.on('end', () => {
      this.switchToSilence();
    });

    voiceStream.on('error', (err) => {
      console.warn('Voice stream error:', err.message);
      this.switchToSilence();
    });
  }

  switchToSilence() {
    this.isReceivingVoice = false;
    this.voiceStream = null;
  }

  _transform(chunk, encoding, callback) {
    // This mixer doesn't transform input chunks directly
    // It manages the flow between silence and voice
    callback();
  }

  destroy() {
    if (this.silenceGenerator) {
      this.silenceGenerator.destroy();
    }
    if (this.voiceStream) {
      try { this.voiceStream.destroy(); } catch (_) {}
    }
    super.destroy();
  }
}

class UserTrackManager {
  constructor(userId, outputDir, format, bitrate, recordingStartTime) {
    this.userId = userId;
    this.outputDir = outputDir;
    this.format = format;
    this.bitrate = bitrate;
    this.recordingStartTime = recordingStartTime;

    // Track user presence and audio state
    this.isPresent = false;
    this.isReceivingAudio = false;
    this.joinTime = null;
    this.leaveTime = null;

    // Audio processing components
    this.audioMixer = new SeamlessAudioMixer();
    this.currentOpusStream = null;
    this.currentDecoder = null;
    this.ffmpegProcess = null;

    // Output file
    const timestamp = Date.now();
    const base = path.join(outputDir, `user_${userId}_continuous`);
    this.filename = format === 'mp3' ? `${base}.mp3` : `${base}.wav`;

    this._setupFFmpeg();
  }

  _setupFFmpeg() {
    const args = [
      '-f', 's16le',
      '-ar', SAMPLE_RATE.toString(),
      '-ac', CHANNELS.toString(),
      '-i', 'pipe:0',
      // Optimized FFmpeg settings for smooth continuous recording
      '-buffer_size', '65536',      // Larger buffer for stability
      '-max_delay', '1000000',      // 1 second max delay
      '-avoid_negative_ts', 'auto', // Handle timestamp issues
      '-fflags', '+genpts'          // Generate presentation timestamps
    ];

    if (this.format === 'mp3') {
      args.push(
        '-b:a', this.bitrate,
        '-f', 'mp3',
        '-write_xing', '0'  // Disable Xing header for better streaming
      );
    } else {
      args.push('-acodec', 'pcm_s16le', '-f', 'wav');
    }

    args.push('-y', this.filename);

    console.log(`[track] Starting FFmpeg for user ${this.userId}`);
    this.ffmpegProcess = spawn('ffmpeg', args, {
      stdio: ['pipe', 'pipe', 'pipe'] // Capture all streams for better error handling
    });

    this.ffmpegProcess.stderr.on('data', (data) => {
      const errorMsg = data.toString();
      // Only log actual errors, not FFmpeg's verbose output
      if (errorMsg.includes('error') || errorMsg.includes('failed')) {
        console.warn(`[track] FFmpeg stderr for user ${this.userId}:`, errorMsg);
      }
    });

    this.ffmpegProcess.on('error', (err) => {
      console.error(`[track] FFmpeg error for user ${this.userId}:`, err.message);
    });

    this.ffmpegProcess.on('close', (code) => {
      console.log(`[track] FFmpeg closed for user ${this.userId} (code: ${code})`);
      this._checkOutputFile();
    });

    // Pipe the audio mixer directly to FFmpeg
    this.audioMixer.pipe(this.ffmpegProcess.stdin);
  }

  userJoined(joinTime = Date.now()) {
    console.log(`[track] User ${this.userId} joined at ${joinTime}`);
    this.isPresent = true;
    this.joinTime = joinTime;
    // Continuous silence is already being generated
  }

  userLeft(leaveTime = Date.now()) {
    console.log(`[track] User ${this.userId} left at ${leaveTime}`);
    this.isPresent = false;
    this.leaveTime = leaveTime;
    this._stopCurrentAudioStream();
  }

  startReceivingAudio(opusStream) {
    console.log(`[track] Starting audio reception for user ${this.userId}`);
    this.isReceivingAudio = true;

    // Stop any existing audio stream
    this._stopCurrentAudioStream();

    this.currentOpusStream = opusStream;
    this.currentDecoder = new prism.opus.Decoder({
      rate: SAMPLE_RATE,
      channels: CHANNELS,
      frameSize: FRAME_SIZE
    });

    // Handle decoder output
    this.currentDecoder.on('data', (pcmChunk) => {
      // Feed PCM data to the mixer as voice input
      if (!this.audioMixer.destroyed) {
        this.audioMixer.switchToVoice(this.currentDecoder);
      }
    });

    this.currentDecoder.on('error', (err) => {
      console.warn(`[track] Decoder error for user ${this.userId}:`, err.message);
      this._stopCurrentAudioStream();
    });

    // Handle opus stream events
    opusStream.on('end', () => {
      console.log(`[track] Opus stream ended for user ${this.userId}`);
      this._stopCurrentAudioStream();
    });

    opusStream.on('error', (err) => {
      console.warn(`[track] Opus stream error for user ${this.userId}:`, err.message);
      this._stopCurrentAudioStream();
    });

    // Connect streams
    opusStream.pipe(this.currentDecoder);
  }

  stopReceivingAudio() {
    console.log(`[track] Stopping audio reception for user ${this.userId}`);
    this._stopCurrentAudioStream();
  }

  _stopCurrentAudioStream() {
    this.isReceivingAudio = false;

    // Switch back to silence
    if (this.audioMixer && !this.audioMixer.destroyed) {
      this.audioMixer.switchToSilence();
    }

    if (this.currentOpusStream) {
      try { this.currentOpusStream.destroy(); } catch (_) {}
      this.currentOpusStream = null;
    }

    if (this.currentDecoder) {
      try { this.currentDecoder.end(); } catch (_) {}
      this.currentDecoder = null;
    }
  }

  cleanup() {
    console.log(`[track] Cleaning up user ${this.userId}`);

    this._stopCurrentAudioStream();

    if (this.audioMixer) {
      try { this.audioMixer.destroy(); } catch (_) {}
    }

    // Give FFmpeg time to finish processing
    setTimeout(() => {
      if (this.ffmpegProcess && !this.ffmpegProcess.killed) {
        try {
          this.ffmpegProcess.stdin.end();
        } catch (_) {}
      }
    }, 3000); // Increased timeout for better file completion
  }

  _checkOutputFile() {
    try {
      if (fs.existsSync(this.filename)) {
        const size = fs.statSync(this.filename).size;
        console.log(`[track] Final file for user ${this.userId}: ${path.basename(this.filename)} (${size} bytes)`);

        if (size < 1000) {
          console.warn(`[track] File too small for user ${this.userId}, but keeping for sync`);
        }
      } else {
        console.warn(`[track] Output file missing for user ${this.userId}: ${this.filename}`);
      }
    } catch (e) {
      console.warn(`[track] Error checking output file for user ${this.userId}:`, e.message);
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
  }

  // --------- Utility: FFmpeg availability ---------
  checkFFmpeg() {
    return new Promise((resolve) => {
      const ffmpeg = spawn('ffmpeg', ['-version'], { stdio: 'ignore' });
      ffmpeg.on('close', (code) => resolve(code === 0));
      ffmpeg.on('error', () => resolve(false));
    });
  }

  // --------- State file helpers ---------
  loadState() {
    try {
      if (fs.existsSync(STATE_FILE)) {
        return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
      }
    } catch (e) {
      console.warn('[state] load error:', e.message);
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
      console.warn('[state] save error:', e.message);
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

  // --------- Client init ---------
  async init() {
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Login timeout after 30s')), 30000);

      const onReady = () => {
        clearTimeout(timeout);
        this.isReady = true;
        console.log(`[recorder] Logged in as ${this.client.user.tag}`);
        resolve();
      };

      this.client.once('clientReady', onReady);
      this.client.once('ready', onReady);

      this.client.on('error', (err) => {
        console.error('[discord] client error:', err);
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

  // --------- Recording core ---------
  async startRecording(guildId, channelId, outputDir, opts = {}) {
    const format = (opts.format || 'wav').toLowerCase();
    const bitrate = opts.bitrate || '192k';

    if (!this.isReady) {
      console.error('[start] client not ready');
      return false;
    }

    const ff = await this.checkFFmpeg();
    if (!ff) {
      console.error('[start] FFmpeg not found on PATH');
      return false;
    }

    const existing = this.checkExistingRecording();
    if (existing) {
      console.warn('[start] another recording is already running:', existing);
      return false;
    }

    const guild = this.client.guilds.cache.get(guildId);
    if (!guild) {
      console.error(`[start] guild not found: ${guildId}`);
      return false;
    }

    const channel = guild.channels.cache.get(channelId);
    if (!channel) {
      console.error(`[start] channel not found: ${channelId}`);
      return false;
    }

    if (channel.type !== 2) {
      console.error(`[start] channel is not a voice channel (type=${channel.type})`);
      return false;
    }

    const me = guild.members.me;
    if (!me) {
      console.error('[start] bot member not found in guild');
      return false;
    }

    const perms = channel.permissionsFor(me);
    if (!perms?.has(PermissionsBitField.Flags.Connect) || !perms?.has(PermissionsBitField.Flags.ViewChannel)) {
      console.error('[start] missing Connect or ViewChannel permission');
      return false;
    }

    console.log('[voice] joining channel...');
    const connection = joinVoiceChannel({
      channelId: channelId,
      guildId: guildId,
      adapterCreator: guild.voiceAdapterCreator,
      selfDeaf: false,
      selfMute: false,
    });

    try {
      await entersState(connection, VoiceConnectionStatus.Ready, 20000);
      console.log('[voice] connection ready');
    } catch (e) {
      console.error('[voice] failed to enter ready state:', e.message);
      try { connection.destroy(); } catch (_) {}
      return false;
    }

    // Keep connection alive
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
    };
    this.activeRecording = rec;

    fs.mkdirSync(outputDir, { recursive: true });

    this.saveState(rec);
    this._setupReceiver(rec);
    this._setupVoiceStateTracking(rec);
    this._monitorStopFlag(rec);

    // Initialize tracks for users already in the channel
    channel.members.forEach(member => {
      if (!member.user.bot) {
        this._initializeUserTrack(rec, member.user.id);
      }
    });

    connection.on(VoiceConnectionStatus.Disconnected, async () => {
      console.log('[voice] disconnected, attempting to recover...');
      try {
        await Promise.race([
          entersState(connection, VoiceConnectionStatus.Signalling, 5000),
          entersState(connection, VoiceConnectionStatus.Connecting, 5000),
        ]);
        console.log('[voice] reconnected');
      } catch (err) {
        console.log('[voice] could not reconnect, destroying...');
        try { connection.destroy(); } catch (_) {}
        this.activeRecording = null;
        this.saveState(null);
      }
    });

    console.log('[start] recording setup complete; listening for audio and tracking users');
    return true;
  }

  _initializeUserTrack(rec, userId) {
    if (!rec.userTracks.has(userId)) {
      console.log(`[recorder] Initializing track for user ${userId}`);
      const trackManager = new UserTrackManager(
        userId,
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
    this.client.on('voiceStateUpdate', (oldState, newState) => {
      if (oldState.channelId !== rec.channelId && newState.channelId !== rec.channelId) {
        return;
      }

      const userId = newState.member.user.id;
      if (newState.member.user.bot) return;

      const now = Date.now();

      if (!oldState.channelId && newState.channelId === rec.channelId) {
        console.log(`[voice] User ${userId} joined recording channel`);
        this._initializeUserTrack(rec, userId);
        const track = rec.userTracks.get(userId);
        if (track && !track.isPresent) {
          track.userJoined(now);
        }
      }
      else if (oldState.channelId === rec.channelId && !newState.channelId) {
        console.log(`[voice] User ${userId} left recording channel`);
        const track = rec.userTracks.get(userId);
        if (track && track.isPresent) {
          track.userLeft(now);
        }
      }
    });
  }

  _setupReceiver(rec) {
    const receiver = rec.connection.receiver;
    console.log('[receiver] setting up speaking listeners');

    receiver.speaking.on('start', (userId) => {
      console.log(`[receiver] User ${userId} started speaking`);

      if (this.client.users.cache.get(userId)?.bot) return;

      this._initializeUserTrack(rec, userId);
      const trackManager = rec.userTracks.get(userId);

      if (!trackManager) {
        console.warn(`[receiver] No track manager for user ${userId}`);
        return;
      }

      try {
        const opusStream = receiver.subscribe(userId, {
          end: { behavior: EndBehaviorType.AfterSilence, duration: 1000 },
        });

        trackManager.startReceivingAudio(opusStream);
      } catch (e) {
        console.error(`[receiver] Failed to subscribe to user ${userId}:`, e.message);
      }
    });

    receiver.speaking.on('end', (userId) => {
      console.log(`[receiver] User ${userId} stopped speaking`);

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
          console.log('[stop] stop flag detected');
          clearInterval(interval);
          await this._stopCurrentRecording();
          console.log('[stop] stopped via flag');
        }
      } catch (e) {
        console.warn('[stop] monitor error:', e.message);
      }
    }, 1000);
  }

  async stopRecording(guildId) {
    if (this.activeRecording && this.activeRecording.guildId === guildId) {
      console.log('[stop] stopping current process recording...');
      return await this._stopCurrentRecording();
    }

    const state = this.loadState();
    if (!state || state.guildId !== guildId) {
      console.log('[stop] no active recording state for this guild');
      return false;
    }

    const stopPath = path.join(state.outputDir, STOP_FILENAME);
    try {
      fs.writeFileSync(stopPath, '');
      console.log(`[stop] wrote stop flag at ${stopPath}`);
    } catch (e) {
      console.warn('[stop] could not write stop flag:', e.message);
    }

    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      const s = this.loadState();
      if (!s || s.guildId !== guildId) {
        console.log('[stop] state cleared (other process stopped)');
        return true;
      }
    }

    console.warn('[stop] other process did not clear state in time');
    try {
      process.kill(state.pid);
      console.warn('[stop] sent terminate to other process');
      this.saveState(null);
      return true;
    } catch (e) {
      console.warn('[stop] terminate failed:', e.message);
      return false;
    }
  }

  async _stopCurrentRecording() {
    if (!this.activeRecording) return false;

    const rec = this.activeRecording;
    console.log('[cleanup] stopping user tracks...');

    // Stop all user tracks
    for (const [userId, trackManager] of rec.userTracks) {
      console.log(`[cleanup] cleaning up track for user ${userId}`);
      trackManager.cleanup();
    }

    console.log('[cleanup] waiting 8s for encoders to finish...');
    await new Promise((r) => setTimeout(r, 8000)); // Increased wait time

    if (rec.player) { try { rec.player.stop(); } catch (_) {} }
    if (rec.connection) { try { rec.connection.destroy(); } catch (_) {} }

    this.activeRecording = null;
    this.saveState(null);
    try { if (fs.existsSync(rec.stopFlagPath)) fs.unlinkSync(rec.stopFlagPath); } catch (_) {}

    console.log('[cleanup] recording stopped');
    return true;
  }

  async cleanup() {
    console.log('[cleanup] global cleanup...');
    if (this.activeRecording) await this._stopCurrentRecording();
    if (this.client && this.client.isReady()) {
      try { await this.client.destroy(); } catch (e) { console.warn('[cleanup] client destroy:', e.message); }
    }
    console.log('[cleanup] done');
  }
}

// ---------------- CLI ----------------
if (require.main === module) {
  const [,, action, ...rest] = process.argv;

  if (!process.env.DISCORD_BOT_TOKEN) {
    console.error('DISCORD_BOT_TOKEN env not set');
    process.exit(1);
  }

  const parseFlags = (args) => {
    const out = { _: [] };
    for (let i = 0; i < args.length; i++) {
      const a = args[i];
      if (a === '--format') { out.format = (args[++i] || 'wav'); continue; }
      if (a === '--bitrate') { out.bitrate = (args[++i] || '192k'); continue; }
      out._.push(a);
    }
    return out;
  };

  const recorder = new VoiceRecorder(process.env.DISCORD_BOT_TOKEN);

  const shutdown = async (signal) => {
    console.log(`\n[proc] received ${signal}, shutdown...`);
    recorder.shouldExit = true;
    await recorder.cleanup();
    process.exit(0);
  };
  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));

  process.on('uncaughtException', async (err) => { console.error('[uncaught]', err); await recorder.cleanup(); process.exit(1); });
  process.on('unhandledRejection', async (reason) => { console.error('[unhandled]', reason); await recorder.cleanup(); process.exit(1); });

  recorder.init().then(async () => {
    switch (action) {
      case 'start': {
        const flags = parseFlags(rest);
        const [guildId, channelId, outputDir] = flags._;
        if (!guildId || !channelId || !outputDir) {
          console.error('Usage: node voice_recorder.js start <guildId> <channelId> <outputDir> [--format wav|mp3] [--bitrate 192k]');
          process.exit(1);
        }

        if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });

        const ok = await recorder.startRecording(guildId, channelId, outputDir, { format: flags.format, bitrate: flags.bitrate });
        if (ok) {
          console.log('[cli] recording started; process will stay alive until stop.flag is created or process is terminated');
          setInterval(() => {}, 1 << 30);
        } else {
          console.error('[cli] failed to start');
          await recorder.cleanup();
          process.exit(1);
        }
        break;
      }
      case 'stop': {
        const [guildId] = rest;
        if (!guildId) {
          console.error('Usage: node voice_recorder.js stop <guildId>');
          process.exit(1);
        }
        const ok = await recorder.stopRecording(guildId);
        console.log(ok ? '[cli] stop requested successfully' : '[cli] stop failed or nothing to stop');
        await recorder.cleanup();
        process.exit(0);
      }
      default:
        console.error('Usage: node voice_recorder.js <start|stop> ...');
        console.error('  start <guildId> <channelId> <outputDir> [--format wav|mp3] [--bitrate 192k]');
        console.error('  stop <guildId>');
        await recorder.cleanup();
        process.exit(1);
    }
  }).catch(async (err) => {
    console.error('[init] failed:', err);
    await recorder.cleanup();
    process.exit(1);
  });
}

module.exports = VoiceRecorder;