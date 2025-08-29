const { Client, GatewayIntentBits } = require('discord.js');
const {
    joinVoiceChannel,
    VoiceConnectionStatus,
    EndBehaviorType,
    entersState
} = require('@discordjs/voice');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

class VoiceRecorder {
    constructor(token) {
        this.client = new Client({
            intents: [
                GatewayIntentBits.Guilds,
                GatewayIntentBits.GuildVoiceStates
            ]
        });
        this.token = token;
        this.recordings = new Map();
        this.isReady = false;
    }

    async init() {
        return new Promise((resolve, reject) => {
            this.client.once('ready', () => {
                console.log(`Voice recorder logged in as ${this.client.user.tag}`);
                console.log(`Bot is in ${this.client.guilds.cache.size} guilds`);
                this.isReady = true;
                resolve();
            });

            this.client.on('error', (error) => {
                console.error('Discord client error:', error);
                if (!this.isReady) {
                    reject(error);
                }
            });

            this.client.login(this.token).catch(reject);
        });
    }

    async startRecording(guildId, channelId, outputDir) {
        try {
            console.log(`Starting recording for guild ${guildId}, channel ${channelId}`);

            const guild = this.client.guilds.cache.get(guildId);
            if (!guild) {
                console.error(`Guild ${guildId} not found`);
                return false;
            }

            const channel = guild.channels.cache.get(channelId);
            if (!channel || channel.type !== 2) {
                console.error(`Voice channel ${channelId} not found or invalid`);
                return false;
            }

            // Check permissions
            const permissions = channel.permissionsFor(guild.members.me);
            if (!permissions?.has(['Connect', 'Speak'])) {
                console.error('Missing voice channel permissions');
                return false;
            }

            // Create connection with correct settings for receiving audio
            const connection = joinVoiceChannel({
                channelId: channelId,
                guildId: guildId,
                adapterCreator: guild.voiceAdapterCreator,
                selfDeaf: false,  // Must be false to receive audio
                selfMute: false   // Can be true, but false is safer for testing
            });

            const recording = {
                connection: connection,
                receiver: null,
                outputDir: outputDir,
                startTime: Date.now(),
                activeStreams: new Map(),
                guildId: guildId
            };

            // Wait for connection to be ready
            try {
                await entersState(connection, VoiceConnectionStatus.Ready, 10000);
                console.log('Voice connection established');
            } catch (error) {
                console.error('Failed to establish voice connection:', error);
                connection.destroy();
                return false;
            }

            // Set up the receiver
            recording.receiver = connection.receiver;
            this.setupAudioReceiver(recording);

            // Handle connection events
            connection.on(VoiceConnectionStatus.Disconnected, async () => {
                console.log('Voice connection lost, attempting to reconnect...');
                try {
                    await entersState(connection, VoiceConnectionStatus.Ready, 5000);
                } catch {
                    connection.destroy();
                    this.recordings.delete(guildId);
                }
            });

            connection.on(VoiceConnectionStatus.Destroyed, () => {
                console.log('Voice connection destroyed');
                this.cleanupRecording(guildId);
            });

            this.recordings.set(guildId, recording);
            console.log('Recording started successfully');
            return true;

        } catch (error) {
            console.error('Failed to start recording:', error);
            return false;
        }
    }

    setupAudioReceiver(recording) {
        const { receiver, outputDir, activeStreams } = recording;

        // Listen for when users start speaking
        receiver.speaking.on('start', (userId) => {
            if (activeStreams.has(userId)) {
                console.log(`User ${userId} already being recorded, skipping`);
                return;
            }

            console.log(`Started recording user ${userId}`);

            // Subscribe to the user's audio stream
            const audioStream = receiver.subscribe(userId, {
                end: {
                    behavior: EndBehaviorType.AfterSilence,
                    duration: 1000 // End after 1 second of silence
                }
            });

            const outputFile = path.join(outputDir, `${userId}_${Date.now()}.wav`);

            // Create FFmpeg process to convert Opus to WAV
            const ffmpegArgs = [
                '-f', 'opus',           // Input format is Opus
                '-ar', '48000',         // Sample rate
                '-ac', '2',             // Stereo
                '-i', 'pipe:0',         // Read from stdin
                '-f', 'wav',            // Output format
                '-acodec', 'pcm_s16le', // PCM encoding
                outputFile              // Output file
            ];

            const ffmpeg = spawn('ffmpeg', ffmpegArgs, {
                stdio: ['pipe', 'pipe', 'pipe']
            });

            // Store the stream info
            activeStreams.set(userId, {
                audioStream,
                ffmpeg,
                outputFile,
                startTime: Date.now()
            });

            // Pipe the Opus audio to FFmpeg
            audioStream.pipe(ffmpeg.stdin);

            // Handle FFmpeg output
            ffmpeg.stdout.on('data', (data) => {
                // FFmpeg stdout (usually empty for this use case)
            });

            ffmpeg.stderr.on('data', (data) => {
                const message = data.toString();
                if (message.includes('error') || message.includes('Error')) {
                    console.error(`FFmpeg error for user ${userId}: ${message}`);
                }
            });

            // Handle stream end
            audioStream.on('end', () => {
                console.log(`Audio stream ended for user ${userId}`);

                setTimeout(() => {
                    if (ffmpeg && !ffmpeg.killed) {
                        ffmpeg.stdin.end();
                    }
                }, 1000);
            });

            // Handle FFmpeg process completion
            ffmpeg.on('close', (code) => {
                console.log(`FFmpeg process for user ${userId} closed with code ${code}`);

                // Check if file was created and has content
                if (fs.existsSync(outputFile)) {
                    const stats = fs.statSync(outputFile);
                    if (stats.size > 1000) { // More than just WAV header
                        console.log(`Successfully recorded ${stats.size} bytes for user ${userId}`);
                    } else {
                        console.log(`Removing empty/invalid file for user ${userId}`);
                        fs.unlinkSync(outputFile);
                    }
                } else {
                    console.log(`No output file created for user ${userId}`);
                }

                activeStreams.delete(userId);
            });

            // Handle errors
            audioStream.on('error', (error) => {
                console.error(`Audio stream error for user ${userId}:`, error);
                if (ffmpeg && !ffmpeg.killed) {
                    ffmpeg.kill();
                }
                activeStreams.delete(userId);
            });

            ffmpeg.on('error', (error) => {
                console.error(`FFmpeg process error for user ${userId}:`, error);
                activeStreams.delete(userId);
            });
        });

        console.log('Audio receiver setup complete');
    }

    async stopRecording(guildId) {
        const recording = this.recordings.get(guildId);
        if (!recording) {
            console.log(`No recording found for guild ${guildId}`);
            return false;
        }

        console.log(`Stopping recording for guild ${guildId}`);

        // Stop all active streams
        for (const [userId, streamData] of recording.activeStreams) {
            try {
                console.log(`Stopping stream for user ${userId}`);
                streamData.audioStream.destroy();

                if (streamData.ffmpeg && !streamData.ffmpeg.killed) {
                    streamData.ffmpeg.stdin.end();
                    setTimeout(() => {
                        if (!streamData.ffmpeg.killed) {
                            streamData.ffmpeg.kill();
                        }
                    }, 2000);
                }
            } catch (error) {
                console.error(`Error stopping stream for user ${userId}:`, error);
            }
        }

        // Wait for processes to finish
        await new Promise(resolve => setTimeout(resolve, 3000));

        // Destroy connection
        if (recording.connection) {
            recording.connection.destroy();
        }

        this.recordings.delete(guildId);
        console.log('Recording stopped successfully');
        return true;
    }

    cleanupRecording(guildId) {
        const recording = this.recordings.get(guildId);
        if (recording) {
            // Force cleanup any remaining streams
            for (const [userId, streamData] of recording.activeStreams) {
                try {
                    streamData.audioStream.destroy();
                    if (streamData.ffmpeg && !streamData.ffmpeg.killed) {
                        streamData.ffmpeg.kill('SIGKILL');
                    }
                } catch (error) {
                    console.warn(`Error during forced cleanup for user ${userId}:`, error);
                }
            }
            this.recordings.delete(guildId);
        }
    }

    async cleanup() {
        console.log('Cleaning up voice recorder...');

        for (const [guildId] of this.recordings) {
            await this.stopRecording(guildId);
        }

        if (this.client?.isReady()) {
            await this.client.destroy();
        }

        console.log('Cleanup complete');
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

    // Graceful shutdown
    const shutdown = async (signal) => {
        console.log(`\nReceived ${signal}, shutting down gracefully...`);
        await recorder.cleanup();
        process.exit(0);
    };

    process.on('SIGINT', () => shutdown('SIGINT'));
    process.on('SIGTERM', () => shutdown('SIGTERM'));

    process.on('uncaughtException', async (error) => {
        console.error('Uncaught exception:', error);
        await recorder.cleanup();
        process.exit(1);
    });

    recorder.init().then(async () => {
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
                }

                const success = await recorder.startRecording(guildId, channelId, outputDir);
                if (!success) {
                    console.error('Failed to start recording');
                    await recorder.cleanup();
                    process.exit(1);
                }
                console.log('Recording is now active. Press Ctrl+C to stop.');
                break;

            case 'stop':
                const [stopGuildId] = args;
                if (!stopGuildId) {
                    console.error('Usage: node voice_recorder.js stop <guildId>');
                    process.exit(1);
                }

                const stopped = await recorder.stopRecording(stopGuildId);
                if (stopped) {
                    console.log('Recording stopped');
                } else {
                    console.log('No active recording found');
                }

                await recorder.cleanup();
                process.exit(0);
                break;

            default:
                console.error('Usage: node voice_recorder.js <start|stop> [args...]');
                process.exit(1);
        }
    }).catch(async (error) => {
        console.error('Failed to initialize recorder:', error);
        await recorder.cleanup();
        process.exit(1);
    });
}