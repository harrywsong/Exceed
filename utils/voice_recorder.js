// voice_recorder.js — FIXED version addressing audio corruption and timing issues
//
// Key fixes:
// 1. Fixed silence generation to use proper sample rate timing
// 2. Corrected audio format pipeline (Opus -> PCM -> FFmpeg)
// 3. Fixed buffer management and stream synchronization
// 4. Proper cleanup to prevent runaway processes
// 5. Added audio validation and error handling

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

// --- Constants (CORRECTED) ---
const STATE_FILE = path.join(process.cwd(), 'recording_state.json');
const STOP_FILENAME = 'stop.flag';
const SAMPLE_RATE = 48000;
const CHANNELS = 2;
const FRAME_SIZE = 960; // 20ms at 48kHz
const BYTES_PER_SAMPLE = 2; // 16-bit
const BYTES_PER_FRAME = FRAME_SIZE * CHANNELS * BYTES_PER_SAMPLE;
const SILENCE_INTERVAL_MS = 20; // Proper 20ms intervals

// Simple silence keepalive
class Silence extends Readable {
  _read() {
    this.push(Buffer.from([0xf8, 0xff, 0xfe]));
  }
}

// FIXED: Precision silence generator with correct timing
class PrecisionSilenceGenerator extends Readable {
  constructor(options = {}) {
    super(options);
    this.sampleRate = SAMPLE_RATE;
    this.channels = CHANNELS;
    this.bytesPerFrame = BYTES_PER_FRAME;
    this.destroyed = false;

    // Calculate exact samples needed for 20ms
    this.samplesPerChunk = Math.floor(this.sampleRate * SILENCE_INTERVAL_MS / 1000);
    this.chunkSize = this.samplesPerChunk * this.channels * BYTES_PER_SAMPLE;

    console.log(`[silence] Generating ${this.chunkSize} bytes every ${SILENCE_INTERVAL_MS}ms`);

    // Use precise interval timing
    this.timer = setInterval(() => this._generateSilenceChunk(), SILENCE_INTERVAL_MS);
  }

  _generateSilenceChunk() {
    if (this.destroyed) return;

    // Generate proper silence buffer - zero bytes for PCM silence
    const silenceChunk = Buffer.alloc(this.chunkSize, 0);
    this.push(silenceChunk);
  }

  _read() {
    // Data is pushed by the timer
  }

  destroy() {
    this.destroyed = true;
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    super.destroy();
  }
}

// FIXED: Simplified audio mixer that doesn't interfere with streams
class AudioMixer extends PassThrough {
  constructor(options = {}) {
    super(options);
    this.isReceivingVoice = false;
    this.silenceGenerator = null;
    this.currentVoiceStream = null;
    this.destroyed = false;

    this._startSilenceMode();
  }

  _startSilenceMode() {
    if (this.destroyed) return;

    console.log('[mixer] Starting silence mode');
    this.isReceivingVoice = false;

    if (this.silenceGenerator) {
      this.silenceGenerator.destroy();
    }

    this.silenceGenerator = new PrecisionSilenceGenerator();
    this.silenceGenerator.on('data', (chunk) => {
      if (!this.isReceivingVoice && !this.destroyed) {
        this.write(chunk);
      }
    });

    this.silenceGenerator.on('error', (err) => {
      console.warn('[mixer] Silence generator error:', err.message);
    });
  }

  switchToVoice(voiceStream) {
    if (this.destroyed) return;

    console.log('[mixer] Switching to voice mode');
    this.isReceivingVoice = true;

    // Stop silence generation
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
      console.log('[mixer] Voice stream ended, back to silence');
      this._startSilenceMode();
    });

    voiceStream.on('error', (err) => {
      console.warn('[mixer] Voice stream error:', err.message);
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
  constructor(userId, outputDir, format, bitrate, recordingStartTime) {
    this.userId = userId;
    this.outputDir = outputDir;
    this.format = format;
    this.bitrate = bitrate;
    this.recordingStartTime = recordingStartTime;

    this.isPresent = false;
    this.isReceivingAudio = false;
    this.joinTime = null;
    this.leaveTime = null;

    // FIXED: Simplified audio pipeline
    this.audioMixer = new AudioMixer();
    this.currentOpusStream = null;
    this.currentDecoder = null;
    this.ffmpegProcess = null;

    const timestamp = Date.now();
    const base = path.join(outputDir, `user_${userId}_continuous`);
    this.filename = format === 'mp3' ? `${base}.mp3` : `${base}.wav`;

    this._setupFFmpeg();
  }

  _setupFFmpeg() {
    // FIXED: Corrected FFmpeg parameters for stable recording
    const args = [
      '-f', 's16le',                    // Input format: signed 16-bit little-endian
      '-ar', SAMPLE_RATE.toString(),    // Sample rate
      '-ac', CHANNELS.toString(),       // Audio channels
      '-i', 'pipe:0',                   // Input from stdin
      '-threads', '1',                  // Single thread for stability
      '-buffer_size', '32768',          // Reasonable buffer size
      '-fflags', '+genpts',             // Generate presentation timestamps
      '-avoid_negative_ts', 'make_zero' // Handle negative timestamps
    ];

    if (this.format === 'mp3') {
      args.push(
        '-acodec', 'libmp3lame',
        '-b:a', this.bitrate,
        '-f', 'mp3'
      );
    } else {
      args.push(
        '-acodec', 'pcm_s16le',
        '-f', 'wav'
      );
    }

    args.push('-y', this.filename);

    console.log(`[track] Starting FFmpeg for user ${this.userId}: ${args.join(' ')}`);

    this.ffmpegProcess = spawn('ffmpeg', args, {
      stdio: ['pipe', 'pipe', 'pipe']
    });

    // FIXED: Better error handling
    this.ffmpegProcess.stderr.on('data', (data) => {
      const errorMsg = data.toString();
      // Only log actual errors
      if (errorMsg.includes('Error') || errorMsg.includes('failed') || errorMsg.includes('Invalid')) {
        console.error(`[ffmpeg] Error for user ${this.userId}:`, errorMsg.trim());
      }
    });

    this.ffmpegProcess.on('error', (err) => {
      console.error(`[ffmpeg] Process error for user ${this.userId}:`, err.message);
    });

    this.ffmpegProcess.on('close', (code) => {
      console.log(`[ffmpeg] Process closed for user ${this.userId} with code ${code}`);
      this._validateOutputFile();
    });

    // FIXED: Proper error handling for pipe
    this.ffmpegProcess.stdin.on('error', (err) => {
      console.warn(`[ffmpeg] Stdin error for user ${this.userId}:`, err.message);
    });

    // Connect the audio mixer to FFmpeg
    this.audioMixer.pipe(this.ffmpegProcess.stdin);
  }

  userJoined(joinTime = Date.now()) {
    console.log(`[track] User ${this.userId} joined at ${new Date(joinTime).toISOString()}`);
    this.isPresent = true;
    this.joinTime = joinTime;
  }

  userLeft(leaveTime = Date.now()) {
    console.log(`[track] User ${this.userId} left at ${new Date(leaveTime).toISOString()}`);
    this.isPresent = false;
    this.leaveTime = leaveTime;
    this._stopCurrentAudioStream();
  }

  startReceivingAudio(opusStream) {
    console.log(`[track] Starting audio reception for user ${this.userId}`);

    // Stop any existing stream first
    this._stopCurrentAudioStream();

    this.isReceivingAudio = true;
    this.currentOpusStream = opusStream;

    // FIXED: Proper Opus decoder configuration
    this.currentDecoder = new prism.opus.Decoder({
      rate: SAMPLE_RATE,
      channels: CHANNELS,
      frameSize: FRAME_SIZE
    });

    // Handle decoder errors
    this.currentDecoder.on('error', (err) => {
      console.error(`[decoder] Error for user ${this.userId}:`, err.message);
      this._stopCurrentAudioStream();
    });

    // Handle opus stream events
    opusStream.on('end', () => {
      console.log(`[opus] Stream ended for user ${this.userId}`);
      this._stopCurrentAudioStream();
    });

    opusStream.on('error', (err) => {
      console.error(`[opus] Stream error for user ${this.userId}:`, err.message);
      this._stopCurrentAudioStream();
    });

    // FIXED: Correct stream pipeline: Opus -> Decoder -> Mixer
    opusStream.pipe(this.currentDecoder);
    this.audioMixer.switchToVoice(this.currentDecoder);
  }

  stopReceivingAudio() {
    console.log(`[track] Stopping audio reception for user ${this.userId}`);
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

    // Mixer will automatically switch back to silence mode
  }

  cleanup() {
    console.log(`[track] Cleaning up user ${this.userId}`);

    this._stopCurrentAudioStream();

    // FIXED: Proper cleanup sequence
    if (this.audioMixer) {
      try {
        this.audioMixer.end();
        this.audioMixer.destroy();
      } catch (_) {}
    }

    // Give FFmpeg time to finish processing buffered data
    if (this.ffmpegProcess && !this.ffmpegProcess.killed) {
      setTimeout(() => {
        try {
          if (!this.ffmpegProcess.killed) {
            this.ffmpegProcess.stdin.end();
          }
        } catch (_) {}
      }, 2000);

      // Force kill if it doesn't close gracefully
      setTimeout(() => {
        try {
          if (!this.ffmpegProcess.killed) {
            this.ffmpegProcess.kill('SIGTERM');
          }
        } catch (_) {}
      }, 8000);
    }
  }

  _validateOutputFile() {
    try {
      if (fs.existsSync(this.filename)) {
        const stats = fs.statSync(this.filename);
        const size = stats.size;
        const durationEstimate = size / (SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE); // rough estimate in seconds

        console.log(`[track] Final file for user ${this.userId}: ${path.basename(this.filename)}`);
        console.log(`[track] Size: ${size} bytes, Estimated duration: ${durationEstimate.toFixed(1)}s`);

        if (size < 1000) {
          console.warn(`[track] File suspiciously small for user ${this.userId}`);
        } else if (durationEstimate > 3600) { // More than 1 hour
          console.warn(`[track] File suspiciously large for user ${this.userId} - possible timing issue`);
        } else {
          console.log(`[track] File appears normal for user ${this.userId}`);
        }
      } else {
        console.warn(`[track] Output file missing for user ${this.userId}: ${this.filename}`);
      }
    } catch (e) {
      console.error(`[track] Error validating output file for user ${this.userId}:`, e.message);
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
      console.warn('[state] Load error:', e.message);
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
      console.warn('[state] Save error:', e.message);
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
        console.error('[discord] Client error:', err);
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

  async startRecording(guildId, channelId, outputDir, opts = {}) {
    const format = (opts.format || 'mp3').toLowerCase();
    const bitrate = opts.bitrate || '192k';

    if (!this.isReady) {
      console.error('[start] Client not ready');
      return false;
    }

    if (!(await this.checkFFmpeg())) {
      console.error('[start] FFmpeg not found on PATH');
      return false;
    }

    const existing = this.checkExistingRecording();
    if (existing) {
      console.warn('[start] Another recording is already running:', existing);
      return false;
    }

    const guild = this.client.guilds.cache.get(guildId);
    if (!guild) {
      console.error(`[start] Guild not found: ${guildId}`);
      return false;
    }

    const channel = guild.channels.cache.get(channelId);
    if (!channel || channel.type !== 2) {
      console.error(`[start] Voice channel not found or invalid: ${channelId}`);
      return false;
    }

    const me = guild.members.me;
    const perms = channel.permissionsFor(me);
    if (!perms?.has(PermissionsBitField.Flags.Connect) || !perms?.has(PermissionsBitField.Flags.ViewChannel)) {
      console.error('[start] Missing required permissions');
      return false;
    }

    console.log('[voice] Joining channel...');
    const connection = joinVoiceChannel({
      channelId: channelId,
      guildId: guildId,
      adapterCreator: guild.voiceAdapterCreator,
      selfDeaf: false,
      selfMute: false,
    });

    try {
      await entersState(connection, VoiceConnectionStatus.Ready, 20000);
      console.log('[voice] Connection ready');
    } catch (e) {
      console.error('[voice] Failed to enter ready state:', e.message);
      try { connection.destroy(); } catch (_) {}
      return false;
    }

    // Keep connection alive with silence
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

    // Initialize tracks for existing users
    channel.members.forEach(member => {
      if (!member.user.bot) {
        this._initializeUserTrack(rec, member.user.id);
      }
    });

    // Handle disconnections
    connection.on(VoiceConnectionStatus.Disconnected, async () => {
      console.log('[voice] Disconnected, attempting to recover...');
      try {
        await Promise.race([
          entersState(connection, VoiceConnectionStatus.Signalling, 5000),
          entersState(connection, VoiceConnectionStatus.Connecting, 5000),
        ]);
        console.log('[voice] Reconnected');
      } catch (err) {
        console.error('[voice] Could not reconnect:', err.message);
        try { connection.destroy(); } catch (_) {}
        await this._stopCurrentRecording();
      }
    });

    console.log('[start] Recording setup complete');
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
    console.log('[receiver] Setting up speaking listeners');

    receiver.speaking.on('start', (userId) => {
      if (this.client.users.cache.get(userId)?.bot) return;

      console.log(`[receiver] User ${userId} started speaking`);
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
          console.log('[stop] Stop flag detected');
          clearInterval(interval);
          await this._stopCurrentRecording();
        }
      } catch (e) {
        console.warn('[stop] Monitor error:', e.message);
      }
    }, 1000);
  }

  async stopRecording(guildId) {
    if (this.activeRecording && this.activeRecording.guildId === guildId) {
      console.log('[stop] Stopping current recording...');
      return await this._stopCurrentRecording();
    }

    const state = this.loadState();
    if (!state || state.guildId !== guildId) {
      console.log('[stop] No active recording state for this guild');
      return false;
    }

    const stopPath = path.join(state.outputDir, STOP_FILENAME);
    try {
      fs.writeFileSync(stopPath, '');
      console.log(`[stop] Wrote stop flag at ${stopPath}`);
    } catch (e) {
      console.warn('[stop] Could not write stop flag:', e.message);
    }

    // Wait for other process to stop
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      const s = this.loadState();
      if (!s || s.guildId !== guildId) {
        console.log('[stop] Other process stopped successfully');
        return true;
      }
    }

    console.warn('[stop] Other process did not stop, attempting to terminate');
    try {
      process.kill(state.pid);
      this.saveState(null);
      return true;
    } catch (e) {
      console.warn('[stop] Terminate failed:', e.message);
      return false;
    }
  }

  async _stopCurrentRecording() {
    if (!this.activeRecording) return false;

    const rec = this.activeRecording;
    console.log('[cleanup] Stopping all user tracks...');

    // Stop all user tracks
    for (const [userId, trackManager] of rec.userTracks) {
      console.log(`[cleanup] Cleaning up track for user ${userId}`);
      trackManager.cleanup();
    }

    // FIXED: Reasonable wait time for cleanup
    console.log('[cleanup] Waiting 5 seconds for encoders to finish...');
    await new Promise((r) => setTimeout(r, 5000));

    if (rec.player) { try { rec.player.stop(); } catch (_) {} }
    if (rec.connection) { try { rec.connection.destroy(); } catch (_) {} }

    this.activeRecording = null;
    this.saveState(null);

    try {
      if (fs.existsSync(rec.stopFlagPath)) {
        fs.unlinkSync(rec.stopFlagPath);
      }
    } catch (_) {}

    console.log('[cleanup] Recording stopped successfully');
    return true;
  }

  async cleanup() {
    console.log('[cleanup] Global cleanup...');
    if (this.activeRecording) {
      await this._stopCurrentRecording();
    }
    if (this.client && this.client.isReady()) {
      try {
        await this.client.destroy();
      } catch (e) {
        console.warn('[cleanup] Client destroy error:', e.message);
      }
    }
    console.log('[cleanup] Cleanup complete');
  }
}

// CLI Interface
if (require.main === module) {
  const [,, action, ...rest] = process.argv;

  if (!process.env.DISCORD_BOT_TOKEN) {
    console.error('DISCORD_BOT_TOKEN environment variable not set');
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
    console.log(`\n[proc] Received ${signal}, shutting down...`);
    recorder.shouldExit = true;
    await recorder.cleanup();
    process.exit(0);
  };

  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));

  process.on('uncaughtException', async (err) => {
    console.error('[uncaught]', err);
    await recorder.cleanup();
    process.exit(1);
  });

  process.on('unhandledRejection', async (reason) => {
    console.error('[unhandled]', reason);
    await recorder.cleanup();
    process.exit(1);
  });

  recorder.init().then(async () => {
    switch (action) {
      case 'start': {
        const flags = parseFlags(rest);
        const [guildId, channelId, outputDir] = flags._;
        if (!guildId || !channelId || !outputDir) {
          console.error('Usage: node voice_recorder.js start <guildId> <channelId> <outputDir> [--format wav|mp3] [--bitrate 192k]');
          process.exit(1);
        }

        if (!fs.existsSync(outputDir)) {
          fs.mkdirSync(outputDir, { recursive: true });
        }

        const ok = await recorder.startRecording(guildId, channelId, outputDir, {
          format: flags.format,
          bitrate: flags.bitrate
        });

        if (ok) {
          console.log('[cli] Recording started successfully');
          // Keep process alive
          setInterval(() => {}, 1 << 30);
        } else {
          console.error('[cli] Failed to start recording');
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
        console.log(ok ? '[cli] Recording stopped successfully' : '[cli] Stop failed or nothing to stop');
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
    console.error('[init] Initialization failed:', err);
    await recorder.cleanup();
    process.exit(1);
  });
}

module.exports = VoiceRecorder;