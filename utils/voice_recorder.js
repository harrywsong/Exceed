// voice_recorder.js — Windows-safe, per-user recording (FFmpeg + prism)
//
// Features
// - Per-user recordings from a Discord voice channel
// - Windows-safe stop (no POSIX signals) via a stop.flag file
// - Robust Opus → PCM decode using prism-media, then FFmpeg → WAV/MP3
// - State persistence to prevent double-recording across processes
// - Reconnect handling and graceful cleanup
// - CLI: start <guildId> <channelId> <outputDir> [--format wav|mp3] [--bitrate 192k]
//
// Requirements
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
const { Readable } = require('stream');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

// --- Constants ---
const STATE_FILE = path.join(process.cwd(), 'recording_state.json');
const STOP_FILENAME = 'stop.flag';

// Keep alive with a single Opus silence frame (Craig-style)
class Silence extends Readable {
  _read() {
    this.push(Buffer.from([0xf8, 0xff, 0xfe]));
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

  // If another process is recording, return its state; clean up stale state otherwise
  checkExistingRecording() {
    const state = this.loadState();
    if (!state) return null;
    try {
      process.kill(state.pid, 0); // check liveness (on Windows this throws if dead)
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

      // discord.js v14+ uses 'clientReady'
      this.client.once('clientReady', onReady);
      // Back-compat just in case user runs older minor
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
    const bitrate = opts.bitrate || '192k'; // for mp3

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

    // 2 is GuildVoice (in discord.js v14, ChannelType.GuildVoice = 2)
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

    // Join voice — selfDeaf must be false to receive
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

    const rec = {
      connection,
      outputDir,
      startTime: Date.now(),
      userStreams: new Map(),
      guildId,
      channelId,
      player,
      format,
      bitrate,
      stopFlagPath: path.join(outputDir, STOP_FILENAME),
    };
    this.activeRecording = rec;

    // Ensure output directory exists
    fs.mkdirSync(outputDir, { recursive: true });

    this.saveState(rec);
    this._setupReceiver(rec);
    this._monitorStopFlag(rec);

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

    console.log('[start] recording setup complete; listening for audio');
    return true;
  }

// Add this enhanced debugging to your _setupReceiver method
  _setupReceiver(rec) {
    const receiver = rec.connection.receiver;
    console.log('[receiver] setting up speaking listeners');

    // Debug: List all current users in voice channel
    const channel = rec.connection.joinConfig.channelId;
    const guild = this.client.guilds.cache.get(rec.guildId);
    const voiceChannel = guild.channels.cache.get(channel);

    console.log(`[receiver] Voice channel members: ${voiceChannel.members.size}`);
    voiceChannel.members.forEach(member => {
      console.log(`[receiver] - ${member.user.username} (${member.user.id}) - Bot: ${member.user.bot}`);
    });

    // Debug: Monitor speaking events more verbosely
    receiver.speaking.on('start', (userId) => {
      console.log(`[receiver] SPEAKING START detected for user ${userId}`);

      // Get user info
      const user = this.client.users.cache.get(userId);
      if (user) {
        console.log(`[receiver] User: ${user.username}#${user.discriminator}`);
      }

      // Guard: already recording this user
      if (rec.userStreams.has(userId)) {
        console.log(`[receiver] user ${userId} already being recorded, skipping`);
        return;
      }

      const ts = Date.now();
      const base = path.join(rec.outputDir, `user_${userId}_${ts}`);
      const filename = rec.format === 'mp3' ? `${base}.mp3` : `${base}.wav`;
      console.log(`[receiver] Creating file: ${filename}`);

      try {
        // Subscribe to Opus packets with more verbose logging
        console.log(`[receiver] Subscribing to audio stream for user ${userId}`);
        const opusStream = receiver.subscribe(userId, {
          end: { behavior: EndBehaviorType.AfterSilence, duration: 2000 },
        });

        // Add immediate data listener to see if we're getting any data
        opusStream.on('data', (chunk) => {
          if (streamData.bytes === 0) {
            console.log(`[receiver] First audio data received for user ${userId}: ${chunk.length} bytes`);
          }
          streamData.bytes += chunk.length;
        });

        // Decode Opus → PCM s16le 48kHz stereo
        const decoder = new prism.opus.Decoder({ rate: 48000, channels: 2, frameSize: 960 });

        // Spawn FFmpeg to encode PCM → WAV/MP3
        const args = ['-f', 's16le', '-ar', '48000', '-ac', '2', '-i', 'pipe:0'];
        if (rec.format === 'mp3') {
          args.push('-b:a', rec.bitrate, '-f', 'mp3', '-y', filename);
        } else {
          args.push('-acodec', 'pcm_s16le', '-f', 'wav', '-y', filename);
        }

        console.log(`[ffmpeg] Starting FFmpeg for ${userId}: ${args.join(' ')}`);
        const ffmpeg = spawn('ffmpeg', args, { stdio: ['pipe', 'inherit', 'inherit'] });

        opusStream.pipe(decoder).pipe(ffmpeg.stdin);

        const streamData = { opusStream, decoder, ffmpeg, filename, startTime: ts, bytes: 0, pcmBytes: 0 };
        rec.userStreams.set(userId, streamData);

        decoder.on('data', (buf) => {
          if (streamData.pcmBytes === 0) {
            console.log(`[receiver] First PCM data decoded for user ${userId}: ${buf.length} bytes`);
          }
          streamData.pcmBytes += buf.length;
        });

        const endAll = () => {
          console.log(`[receiver] Ending stream for ${userId} - Total Opus: ${streamData.bytes}b, PCM: ${streamData.pcmBytes}b`);
          try { opusStream.destroy(); } catch (_) {}
          try { decoder.end(); } catch (_) {}
          if (ffmpeg.stdin && !ffmpeg.stdin.destroyed) {
            try {
              ffmpeg.stdin.end();
              console.log(`[ffmpeg] FFmpeg stdin ended for ${userId}`);
            } catch (_) {}
          }
        };

        opusStream.on('end', () => {
          console.log(`[receiver] OpusStream ended for ${userId}`);
          endAll();
        });

        opusStream.on('close', () => {
          console.log(`[receiver] OpusStream closed for ${userId}`);
          endAll();
        });

        opusStream.on('error', (e) => {
          console.warn(`[receiver] OpusStream error ${userId}:`, e.message);
          endAll();
        });

        decoder.on('error', (e) => {
          console.warn(`[receiver] Decoder error ${userId}:`, e.message);
          endAll();
        });

        ffmpeg.on('close', (code) => {
          console.log(`[ffmpeg] Process closed for ${userId} (exit code: ${code})`);
          console.log(`[ffmpeg] Final stats for ${userId} - Opus: ${streamData.bytes}b, PCM: ${streamData.pcmBytes}b`);

          try {
            if (fs.existsSync(filename)) {
              const size = fs.statSync(filename).size;
              console.log(`[ffmpeg] Output file size: ${size} bytes`);

              if (size < 100) {
                console.warn(`[ffmpeg] Deleting empty file for ${userId} (${size} bytes)`);
                fs.unlinkSync(filename);
              } else {
                console.log(`[ffmpeg] Successfully created: ${path.basename(filename)} (${size} bytes)`);
              }
            } else {
              console.warn(`[ffmpeg] Output file does not exist: ${filename}`);
            }
          } catch (e) {
            console.warn(`[ffmpeg] File handling error for ${userId}:`, e.message);
          }
          rec.userStreams.delete(userId);
        });

        ffmpeg.on('error', (e) => {
          console.error(`[ffmpeg] Process error for ${userId}:`, e.message);
          endAll();
        });

      } catch (e) {
        console.error(`[receiver] Setup failure for ${userId}:`, e.message);
      }
    });

    receiver.speaking.on('end', (userId) => {
      const user = this.client.users.cache.get(userId);
      const username = user ? user.username : 'Unknown';
      console.log(`[receiver] SPEAKING END detected for user ${userId} (${username})`);
    });

    // Additional debugging: Monitor connection state
    rec.connection.on('stateChange', (oldState, newState) => {
      console.log(`[voice] Connection state changed: ${oldState.status} -> ${newState.status}`);
    });

    // Log initial connection state
    console.log(`[voice] Initial connection state: ${rec.connection.state.status}`);
  }  _monitorStopFlag(rec) {
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
    // If current process owns the recording
    if (this.activeRecording && this.activeRecording.guildId === guildId) {
      console.log('[stop] stopping current process recording...');
      return await this._stopCurrentRecording();
    }

    const state = this.loadState();
    if (!state || state.guildId !== guildId) {
      console.log('[stop] no active recording state for this guild');
      return false;
    }

    // Cross-process stop: write stop.flag in outputDir
    const stopPath = path.join(state.outputDir, STOP_FILENAME);
    try {
      fs.writeFileSync(stopPath, '');
      console.log(`[stop] wrote stop flag at ${stopPath}`);
    } catch (e) {
      console.warn('[stop] could not write stop flag:', e.message);
    }

    // Wait up to 8s for state to clear
    for (let i = 0; i < 16; i++) {
      await new Promise((r) => setTimeout(r, 500));
      const s = this.loadState();
      if (!s || s.guildId !== guildId) {
        console.log('[stop] state cleared (other process stopped)');
        return true;
      }
    }

    console.warn('[stop] other process did not clear state in time (it may still be stopping)');
    // As a last resort, try to terminate (works on Windows but may be abrupt)
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
    console.log('[cleanup] stopping user streams...');

    for (const [userId, s] of rec.userStreams) {
      console.log(`[cleanup] user ${userId}`);
      try { s.opusStream?.destroy(); } catch (_) {}
      try { s.decoder?.end(); } catch (_) {}
      try {
        if (s.ffmpeg && s.ffmpeg.stdin && !s.ffmpeg.stdin.destroyed) s.ffmpeg.stdin.end();
        setTimeout(() => {
          try { s.ffmpeg?.kill('SIGTERM'); } catch (_) {}
        }, 1500);
      } catch (_) {}
    }

    console.log('[cleanup] waiting 3s for encoders to flush...');
    await new Promise((r) => setTimeout(r, 3000));

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

  // Parse simple flags from tail (supports --format, --bitrate)
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
          // Keep process alive
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
