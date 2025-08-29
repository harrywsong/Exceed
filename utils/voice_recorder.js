const { Client, GatewayIntentBits } = require('discord.js');
const {
    joinVoiceChannel,
    VoiceConnectionStatus,
    EndBehaviorType,
    entersState,
    createAudioPlayer,
    createAudioResource,
    AudioPlayerStatus
} = require('@discordjs/voice');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

// Add state file management
const STATE_FILE = path.join(process.cwd(), 'recording_state.json');

class VoiceRecorder {
    constructor(token) {
        this.client = new Client({
            intents: [
                GatewayIntentBits.Guilds,
                GatewayIntentBits.GuildVoiceStates
            ]
        });
        this.token = token;
        this.activeRecording = null;
        this.isReady = false;
        this.shouldExit = false;
    }

    // Load state from file
    loadState() {
        try {
            if (fs.existsSync(STATE_FILE)) {
                const state = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
                console.log('Loaded existing state:', state);
                return state;
            }
        } catch (error) {
            console.warn('Error loading state:', error.message);
        }
        return null;
    }

    // Save state to file
    saveState(recording) {
        try {
            const state = recording ? {
                guildId: recording.guildId,
                channelId: recording.channelId,
                outputDir: recording.outputDir,
                startTime: recording.startTime,
                pid: process.pid
            } : null;

            if (state) {
                fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
                console.log('State saved:', state);
            } else {
                // Remove state file when no recording
                if (fs.existsSync(STATE_FILE)) {
                    fs.unlinkSync(STATE_FILE);
                    console.log('State file removed');
                }
            }
        } catch (error) {
            console.warn('Error saving state:', error.message);
        }
    }

    // Check if another process is recording
    checkExistingRecording() {
        const state = this.loadState();
        if (!state) return null;

        // Check if the process is still running
        try {
            process.kill(state.pid, 0); // Signal 0 just checks if process exists
            console.log(`Found existing recording process (PID: ${state.pid})`);
            return state;
        } catch (error) {
            console.log('Previous recording process no longer exists');
            this.saveState(null); // Clean up stale state
            return null;
        }
    }

    async init() {
        return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => {
                reject(new Error('Login timeout after 30 seconds'));
            }, 30000);

            const onReady = () => {
                clearTimeout(timeout);
                console.log(`Voice recorder logged in as ${this.client.user.tag}`);
                console.log(`Bot is in ${this.client.guilds.cache.size} guilds`);
                this.isReady = true;
                resolve();
            };

            // Handle both old and new Discord.js versions
            this.client.once('ready', onReady);
            this.client.once('clientReady', onReady);

            this.client.on('error', (error) => {
                console.error('Discord client error:', error);
                if (!this.isReady) {
                    clearTimeout(timeout);
                    reject(error);
                }
            });

            this.client.login(this.token).catch((error) => {
                clearTimeout(timeout);
                reject(error);
            });
        });
    }

    async startRecording(guildId, channelId, outputDir) {
        try {
            console.log(`Attempting to start recording for guild ${guildId}, channel ${channelId}`);

            // Check if there's already a recording
            const existingState = this.checkExistingRecording();
            if (existingState) {
                console.log('Recording already in progress by another process');
                return false;
            }

            if (!this.isReady) {
                console.error('Client is not ready');
                return false;
            }

            const guild = this.client.guilds.cache.get(guildId);
            if (!guild) {
                console.error(`Guild ${guildId} not found`);
                console.log('Available guilds:', Array.from(this.client.guilds.cache.values()).map(g => `${g.name} (${g.id})`));
                return false;
            }

            const channel = guild.channels.cache.get(channelId);
            if (!channel) {
                console.error(`Channel ${channelId} not found`);
                console.log('Available channels:', Array.from(guild.channels.cache.values()).map(c => `${c.name} (${c.id}) type:${c.type}`));
                return false;
            }

            if (channel.type !== 2) {
                console.error(`Channel ${channel.name} is not a voice channel (type: ${channel.type})`);
                return false;
            }

            console.log(`Found voice channel: ${channel.name}`);

            // Check permissions
            const botMember = guild.members.me;
            if (!botMember) {
                console.error('Bot member not found in guild');
                return false;
            }

            const permissions = channel.permissionsFor(botMember);
            console.log(`Bot permissions - Connect: ${permissions.has('Connect')}, Speak: ${permissions.has('Speak')}, ViewChannel: ${permissions.has('ViewChannel')}`);

            if (!permissions.has('Connect') || !permissions.has('ViewChannel')) {
                console.error('Bot lacks required permissions');
                return false;
            }

            console.log('Creating voice connection...');

            // Create voice connection
            const connection = joinVoiceChannel({
                channelId: channelId,
                guildId: guildId,
                adapterCreator: guild.voiceAdapterCreator,
                selfDeaf: false,  // MUST be false to receive audio
                selfMute: true    // We don't need to send audio
            });

            console.log('Voice connection created, waiting for ready state...');

            // Wait for connection to be ready with longer timeout
            try {
                await entersState(connection, VoiceConnectionStatus.Ready, 20000);
                console.log('Voice connection is ready!');
            } catch (error) {
                console.error('Failed to connect to voice channel:', error.message);
                connection.destroy();
                return false;
            }

            // Set up recording
            const recording = {
                connection: connection,
                outputDir: outputDir,
                startTime: Date.now(),
                userStreams: new Map(),
                guildId: guildId,
                channelId: channelId
            };

            this.activeRecording = recording;
            this.saveState(recording); // Save state to file
            this.setupRecording(recording);

            console.log('Recording setup complete - now listening for audio');
            return true;

        } catch (error) {
            console.error('Error starting recording:', error);
            return false;
        }
    }

    setupRecording(recording) {
        const receiver = recording.connection.receiver;

        console.log('Setting up audio receiver...');

        // Listen for users speaking
        receiver.speaking.on('start', (userId) => {
            console.log(`User ${userId} started speaking`);

            // Skip if we're already recording this user
            if (recording.userStreams.has(userId)) {
                return;
            }

            try {
                // Create audio stream for this user
                const audioStream = receiver.subscribe(userId, {
                    end: {
                        behavior: EndBehaviorType.AfterSilence,
                        duration: 1000
                    }
                });

                const timestamp = Date.now();
                const filename = path.join(recording.outputDir, `user_${userId}_${timestamp}.wav`);

                console.log(`Starting recording for user ${userId} to file: ${path.basename(filename)}`);

                // Create FFmpeg process to convert Opus to WAV
                const ffmpeg = spawn('ffmpeg', [
                    '-hide_banner',
                    '-loglevel', 'error',
                    '-f', 'opus',
                    '-ar', '48000',
                    '-ac', '2',
                    '-i', 'pipe:0',
                    '-f', 'wav',
                    '-ar', '48000',
                    '-ac', '2',
                    filename
                ], {
                    stdio: ['pipe', 'pipe', 'pipe']
                });

                recording.userStreams.set(userId, {
                    audioStream,
                    ffmpeg,
                    filename,
                    startTime: timestamp
                });

                // Pipe audio to FFmpeg
                audioStream.pipe(ffmpeg.stdin);

                // Handle FFmpeg completion
                ffmpeg.on('close', (code) => {
                    console.log(`FFmpeg for user ${userId} finished with code ${code}`);

                    // Check if file exists and has content
                    if (fs.existsSync(filename)) {
                        const stats = fs.statSync(filename);
                        if (stats.size > 1000) {
                            console.log(`Successfully recorded ${stats.size} bytes for user ${userId}`);
                        } else {
                            console.log(`Removing empty file for user ${userId}`);
                            fs.unlinkSync(filename);
                        }
                    }

                    recording.userStreams.delete(userId);
                });

                ffmpeg.stderr.on('data', (data) => {
                    const error = data.toString();
                    if (error.includes('Error') || error.includes('error')) {
                        console.error(`FFmpeg error for user ${userId}: ${error.trim()}`);
                    }
                });

                ffmpeg.on('error', (error) => {
                    console.error(`FFmpeg process error for user ${userId}:`, error.message);
                    recording.userStreams.delete(userId);
                });

                // Handle audio stream events
                audioStream.on('error', (error) => {
                    console.error(`Audio stream error for user ${userId}:`, error.message);
                    if (ffmpeg && !ffmpeg.killed) {
                        ffmpeg.kill();
                    }
                    recording.userStreams.delete(userId);
                });

                audioStream.on('end', () => {
                    console.log(`Audio stream ended for user ${userId}`);
                    if (ffmpeg && !ffmpeg.killed && ffmpeg.stdin.writable) {
                        ffmpeg.stdin.end();
                    }
                });

            } catch (error) {
                console.error(`Error setting up recording for user ${userId}:`, error.message);
            }
        });

        // Handle connection events
        recording.connection.on(VoiceConnectionStatus.Disconnected, async () => {
            console.log('Voice connection disconnected, attempting to reconnect...');
            try {
                await entersState(recording.connection, VoiceConnectionStatus.Ready, 5000);
                console.log('Reconnected successfully');
            } catch {
                console.log('Failed to reconnect, destroying connection');
                recording.connection.destroy();
            }
        });

        recording.connection.on(VoiceConnectionStatus.Destroyed, () => {
            console.log('Voice connection destroyed');
            this.activeRecording = null;
            this.saveState(null); // Clear state when connection destroyed
        });

        recording.connection.on('error', (error) => {
            console.error('Voice connection error:', error);
            this.cleanup();
        });

        console.log('Audio receiver setup complete');
    }

    async stopRecording(guildId) {
        // First check if this process has an active recording
        if (this.activeRecording && this.activeRecording.guildId === guildId) {
            console.log('Stopping recording in current process...');
            return await this._stopCurrentRecording();
        }

        // Check if another process has the recording
        const existingState = this.loadState();
        if (!existingState || existingState.guildId !== guildId) {
            console.log('No active recording found for guild', guildId);
            return false;
        }

        // Try to stop the other process
        console.log(`Found recording in process ${existingState.pid}, sending stop signal...`);
        try {
            // Send SIGUSR1 to signal the recording process to stop
            process.kill(existingState.pid, 'SIGUSR1');

            // Wait a bit for the process to handle the signal
            await new Promise(resolve => setTimeout(resolve, 3000));

            // Check if state was cleared (indicating successful stop)
            const newState = this.loadState();
            if (!newState || newState.guildId !== guildId) {
                console.log('Recording stopped successfully by other process');
                return true;
            } else {
                console.log('Other process did not stop recording, trying SIGTERM...');
                process.kill(existingState.pid, 'SIGTERM');
                await new Promise(resolve => setTimeout(resolve, 2000));
                this.saveState(null); // Force clear state
                return true;
            }
        } catch (error) {
            console.warn('Error stopping other process:', error.message);
            this.saveState(null); // Clear stale state
            return false;
        }
    }

    async _stopCurrentRecording() {
        if (!this.activeRecording) {
            return false;
        }

        console.log('Stopping recording...');
        const recording = this.activeRecording;

        // Stop all user streams
        for (const [userId, streamData] of recording.userStreams) {
            console.log(`Stopping stream for user ${userId}`);
            try {
                if (streamData.audioStream) {
                    streamData.audioStream.destroy();
                }
                if (streamData.ffmpeg && !streamData.ffmpeg.killed) {
                    streamData.ffmpeg.stdin.end();
                    setTimeout(() => {
                        if (!streamData.ffmpeg.killed) {
                            streamData.ffmpeg.kill();
                        }
                    }, 2000);
                }
            } catch (error) {
                console.warn(`Error stopping stream for user ${userId}:`, error.message);
            }
        }

        // Wait for processes to finish
        await new Promise(resolve => setTimeout(resolve, 3000));

        // Destroy connection
        if (recording.connection) {
            recording.connection.destroy();
        }

        this.activeRecording = null;
        this.saveState(null); // Clear state file
        console.log('Recording stopped');
        return true;
    }

    async cleanup() {
        console.log('Cleaning up recorder...');

        if (this.activeRecording) {
            await this._stopCurrentRecording();
        }

        if (this.client && this.client.isReady()) {
            try {
                await this.client.destroy();
            } catch (error) {
                console.warn('Error destroying client:', error.message);
            }
        }

        console.log('Cleanup complete');
    }

    // Keep the process alive while recording
    keepAlive() {
        const checkInterval = setInterval(() => {
            if (this.shouldExit || !this.activeRecording) {
                console.log('No active recording or exit requested, stopping...');
                clearInterval(checkInterval);
                this.cleanup().then(() => {
                    process.exit(0);
                });
            }
        }, 5000);
    }
}

// CLI interface
if (require.main === module) {
    const [,, action, ...args] = process.argv;

    if (!process.env.DISCORD_BOT_TOKEN) {
        console.error('DISCORD_BOT_TOKEN environment variable not set');
        process.exit(1);
    }

    const recorder = new VoiceRecorder(process.env.DISCORD_BOT_TOKEN);

    // Graceful shutdown handlers
    const shutdown = async (signal) => {
        console.log(`\nReceived ${signal}, shutting down gracefully...`);
        recorder.shouldExit = true;
        await recorder.cleanup();
        process.exit(0);
    };

    // Handle stop signal from other processes
    process.on('SIGUSR1', async () => {
        console.log('Received stop signal from another process');
        if (recorder.activeRecording) {
            await recorder._stopCurrentRecording();
        }
        console.log('Recording stopped by external signal');
    });

    process.on('SIGINT', () => shutdown('SIGINT'));
    process.on('SIGTERM', () => shutdown('SIGTERM'));

    process.on('uncaughtException', async (error) => {
        console.error('Uncaught exception:', error);
        await recorder.cleanup();
        process.exit(1);
    });

    process.on('unhandledRejection', async (reason, promise) => {
        console.error('Unhandled rejection:', reason);
        await recorder.cleanup();
        process.exit(1);
    });

    recorder.init().then(async () => {
        console.log('Recorder initialized successfully');

        switch(action) {
            case 'start':
                const [guildId, channelId, outputDir] = args;
                if (!guildId || !channelId || !outputDir) {
                    console.error('Usage: node voice_recorder.js start <guildId> <channelId> <outputDir>');
                    process.exit(1);
                }

                // Ensure output directory exists
                if (!fs.existsSync(outputDir)) {
                    fs.mkdirSync(outputDir, { recursive: true });
                    console.log(`Created output directory: ${outputDir}`);
                }

                console.log('Attempting to start recording...');
                const success = await recorder.startRecording(guildId, channelId, outputDir);

                if (success) {
                    console.log('Recording started successfully! Keeping process alive...');
                    recorder.keepAlive();
                } else {
                    console.error('Failed to start recording');
                    await recorder.cleanup();
                    process.exit(1);
                }
                break;

            case 'stop':
                const [stopGuildId] = args;
                if (!stopGuildId) {
                    console.error('Usage: node voice_recorder.js stop <guildId>');
                    process.exit(1);
                }

                const stopped = await recorder.stopRecording(stopGuildId);
                if (stopped) {
                    console.log('Recording stopped successfully');
                } else {
                    console.log('No active recording found to stop');
                }

                await recorder.cleanup();
                process.exit(0);
                break;

            default:
                console.error('Usage: node voice_recorder.js <start|stop> [args...]');
                console.error('Commands:');
                console.error('  start <guildId> <channelId> <outputDir> - Start recording');
                console.error('  stop <guildId> - Stop recording');
                process.exit(1);
        }
    }).catch(async (error) => {
        console.error('Failed to initialize recorder:', error);
        await recorder.cleanup();
        process.exit(1);
    });
}

module.exports = VoiceRecorder;