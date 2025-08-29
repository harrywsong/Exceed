const { Client, GatewayIntentBits } = require('discord.js');
const {
    joinVoiceChannel,
    VoiceConnectionStatus,
    EndBehaviorType,
} = require('@discordjs/voice');
const prism = require('prism-media');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { entersState } = require('@discordjs/voice');


class VoiceRecorder {
    constructor(token) {
        this.client = new Client({
            intents: [
                GatewayIntentBits.Guilds,
                GatewayIntentBits.GuildVoiceStates,
                GatewayIntentBits.GuildMessages  // Added this intent
            ]
        });
        this.token = token;
        this.recordings = new Map();
        this.isRaspberryPi = this.detectRaspberryPi();
        this.isReady = false;
    }

    detectRaspberryPi() {
        try {
            const fs = require('fs');
            if (fs.existsSync('/proc/cpuinfo')) {
                const cpuinfo = fs.readFileSync('/proc/cpuinfo', 'utf8');
                return cpuinfo.includes('Raspberry Pi') || cpuinfo.includes('BCM');
            }
        } catch (error) {
            console.log('Could not detect Pi, assuming regular Linux');
        }
        return false;
    }

    async init() {
        return new Promise((resolve, reject) => {
            // Set up event listeners before login
            this.client.once('clientReady', () => {
                console.log(`Voice recorder logged in as ${this.client.user.tag}`);
                if (this.isRaspberryPi) {
                    console.log('🥧 Raspberry Pi detected - using optimized settings');
                }

                // Log guild and channel information for debugging
                console.log(`Bot is in ${this.client.guilds.cache.size} guilds:`);
                this.client.guilds.cache.forEach(guild => {
                    console.log(`- ${guild.name} (${guild.id}) - ${guild.channels.cache.size} channels`);
                });

                this.isReady = true;
                resolve();
            });

            this.client.on('error', (error) => {
                console.error('Discord client error:', error);
                if (!this.isReady) {
                    reject(error);
                }
            });

            this.client.on('disconnect', () => {
                console.log('Discord client disconnected');
                this.isReady = false;
            });

            // Login with error handling
            this.client.login(this.token).catch(reject);
        });
    }

    async startRecording(guildId, channelId, outputDir) {
        try {
            // Wait for client to be ready
            if (!this.isReady) {
                console.log('Waiting for Discord client to be ready...');
                let attempts = 0;
                while (!this.isReady && attempts < 30) {
                    await new Promise(resolve => setTimeout(resolve, 1000));
                    attempts++;
                }
                if (!this.isReady) {
                    throw new Error('Discord client not ready after 30 seconds');
                }
            }

            console.log(`Looking for guild ${guildId}...`);
            const guild = this.client.guilds.cache.get(guildId);
            if (!guild) {
                console.error(`Guild ${guildId} not found in cache`);
                console.log('Available guilds:', this.client.guilds.cache.map(g => `${g.name} (${g.id})`));
                return false;
            }

            console.log(`Found guild: ${guild.name}. Looking for channel ${channelId}...`);

            // Force fetch channels if not in cache
            try {
                await guild.channels.fetch();
            } catch (fetchError) {
                console.warn(`Could not fetch channels: ${fetchError.message}`);
            }

            const channel = guild.channels.cache.get(channelId);
            if (!channel) {
                console.error(`Channel ${channelId} not found in guild ${guild.name}`);
                console.log('Available voice channels:',
                    guild.channels.cache
                        .filter(c => c.type === 2) // Voice channels
                        .map(c => `${c.name} (${c.id})`)
                );
                return false;
            }

            if (channel.type !== 2) { // Not a voice channel
                console.error(`Channel ${channel.name} is not a voice channel (type: ${channel.type})`);
                return false;
            }

            console.log(`Starting recording in voice channel: ${channel.name}`);

            // Check if bot has necessary permissions
            const permissions = channel.permissionsFor(guild.members.me);
            if (!permissions.has('Connect') || !permissions.has('Speak')) {
                console.error(`Bot lacks permissions in channel ${channel.name}. Connect: ${permissions.has('Connect')}, Speak: ${permissions.has('Speak')}`);
                return false;
            }

            const connection = joinVoiceChannel({
                channelId: channelId,
                guildId: guildId,
                adapterCreator: guild.voiceAdapterCreator,
                selfDeaf: true,
                selfMute: false
            });

            const recording = {
                connection: connection,
                users: new Map(),
                outputDir: outputDir,
                startTime: Date.now(),
                guildId: guildId,
                channelId: channelId
            };

            // Set up connection event handlers with timeout
            const connectionTimeout = setTimeout(() => {
                console.error('Voice connection timeout after 10 seconds');
                connection.destroy();
            }, 10000);

            connection.on(VoiceConnectionStatus.Ready, () => {
                clearTimeout(connectionTimeout);
                console.log('Voice connection ready, setting up recording');
                this.setupUserRecording(connection, recording);
            });

            connection.on(VoiceConnectionStatus.Disconnected, async (oldState, newState) => {
                clearTimeout(connectionTimeout);
                console.log('Voice connection disconnected');

                try {
                    await Promise.race([
                        entersState(connection, VoiceConnectionStatus.Signalling, 5_000),
                        entersState(connection, VoiceConnectionStatus.Connecting, 5_000),
                    ]);
                } catch (error) {
                    console.log('Connection could not be re-established, destroying...');
                    connection.destroy();
                    this.recordings.delete(guildId);
                }
            });

            connection.on(VoiceConnectionStatus.Destroyed, () => {
                clearTimeout(connectionTimeout);
                console.log('Voice connection destroyed');
                this.recordings.delete(guildId);
            });

            connection.on('error', (error) => {
                clearTimeout(connectionTimeout);
                console.error('Voice connection error:', error);
                connection.destroy();
                this.recordings.delete(guildId);
            });

            this.recordings.set(guildId, recording);
            return true;
        } catch (error) {
            console.error('Failed to start recording:', error);
            return false;
        }
    }

    setupUserRecording(connection, recording) {
        const receiver = connection.receiver;

        receiver.speaking.on('start', (userId) => {
            if (recording.users.has(userId)) return;

            console.log(`User ${userId} started speaking`);

            const userStream = receiver.subscribe(userId, {
                end: {
                    behavior: EndBehaviorType.AfterSilence,
                    duration: this.isRaspberryPi ? 200 : 100, // Longer silence for Pi
                },
            });

            const filename = path.join(recording.outputDir, `${userId}.pcm`);
            const fileStream = fs.createWriteStream(filename);

            // Pi-optimized opus decoder settings
            const decoderOptions = this.isRaspberryPi ? {
                frameSize: 960,
                channels: 1, // Mono to save CPU on Pi
                rate: 48000
            } : {
                frameSize: 960,
                channels: 2,
                rate: 48000
            };

            const opusDecoder = new prism.opus.Decoder(decoderOptions);

            userStream.pipe(opusDecoder).pipe(fileStream);

            recording.users.set(userId, {
                stream: userStream,
                file: filename,
                startTime: Date.now()
            });

            userStream.on('end', () => {
                console.log(`User ${userId} stopped speaking`);
                fileStream.end();
            });

            userStream.on('error', (error) => {
                console.error(`Stream error for user ${userId}:`, error);
                fileStream.end();
            });
        });
    }

    async stopRecording(guildId) {
        const recording = this.recordings.get(guildId);
        if (!recording) {
            console.log('No recording found for guild', guildId);
            return false;
        }

        console.log('Stopping recording and processing audio');

        // Stop all user streams
        for (const [userId, userData] of recording.users) {
            try {
                userData.stream.destroy();
            } catch (error) {
                console.warn(`Error stopping stream for user ${userId}:`, error);
            }
        }

        // Disconnect from voice
        recording.connection.destroy();

        // Process the recording files
        await this.processRecording(recording);

        this.recordings.delete(guildId);
        return true;
    }

    async processRecording(recording) {
        console.log('Processing recorded audio files');

        const processPromises = [];

        for (const [userId, userData] of recording.users) {
            const pcmFile = userData.file;
            const wavFile = pcmFile.replace('.pcm', '.wav');

            if (fs.existsSync(pcmFile)) {
                const stats = fs.statSync(pcmFile);
                if (stats.size > 0) {
                    if (this.isRaspberryPi) {
                        // Process files sequentially on Pi to avoid overload
                        await this.convertPcmToWav(pcmFile, wavFile);
                    } else {
                        processPromises.push(this.convertPcmToWav(pcmFile, wavFile));
                    }
                } else {
                    console.log(`Skipping empty file for user ${userId}`);
                    try { fs.unlinkSync(pcmFile); } catch {}
                }
            }
        }

        if (!this.isRaspberryPi && processPromises.length > 0) {
            try {
                await Promise.all(processPromises);
                console.log('All audio files processed successfully');
            } catch (error) {
                console.error('Error processing audio files:', error);
            }
        }
    }

    async convertPcmToWav(pcmFile, wavFile) {
        return new Promise((resolve, reject) => {
            console.log(`Converting ${pcmFile} to ${wavFile}`);

            // Check if PCM file exists and has content
            if (!fs.existsSync(pcmFile)) {
                console.log(`PCM file ${pcmFile} does not exist, skipping conversion`);
                resolve();
                return;
            }

            const stats = fs.statSync(pcmFile);
            if (stats.size === 0) {
                console.log(`PCM file ${pcmFile} is empty, skipping conversion`);
                try { fs.unlinkSync(pcmFile); } catch {}
                resolve();
                return;
            }

            // Pi-optimized FFmpeg settings
            const ffmpegArgs = [
                '-f', 's16le',
                '-ar', '48000',
                '-ac', this.isRaspberryPi ? '1' : '2', // Mono for Pi
                '-i', pcmFile,
                '-acodec', 'pcm_s16le'
            ];

            // Pi-specific optimizations
            if (this.isRaspberryPi) {
                ffmpegArgs.push('-threads', '2'); // Limit threads
                ffmpegArgs.push('-preset', 'ultrafast'); // Fastest encoding
            }

            ffmpegArgs.push(wavFile, '-y');

            console.log(`Running FFmpeg with args: ${ffmpegArgs.join(' ')}`);
            const ffmpeg = spawn('ffmpeg', ffmpegArgs);

            let stderr = '';
            ffmpeg.stderr.on('data', (data) => {
                stderr += data.toString();
            });

            ffmpeg.stdout.on('data', (data) => {
                // Log FFmpeg output for debugging
                console.log(`FFmpeg stdout: ${data.toString()}`);
            });

            ffmpeg.on('close', (code) => {
                console.log(`FFmpeg process exited with code ${code}`);
                if (code === 0) {
                    try {
                        // Check if WAV file was created successfully
                        if (fs.existsSync(wavFile) && fs.statSync(wavFile).size > 0) {
                            fs.unlinkSync(pcmFile); // Delete PCM only if WAV was created
                            console.log(`✅ Successfully converted ${path.basename(pcmFile)} to ${path.basename(wavFile)}`);
                        } else {
                            console.log(`⚠️  WAV file not created or empty, keeping PCM file ${path.basename(pcmFile)}`);
                        }
                        resolve();
                    } catch (err) {
                        console.warn(`Could not delete PCM file: ${err.message}`);
                        resolve();
                    }
                } else {
                    console.error(`❌ FFmpeg failed with code ${code}:`);
                    if (stderr) console.error(`FFmpeg stderr: ${stderr}`);
                    // Don't reject - keep the PCM file if conversion fails
                    console.log(`Keeping original PCM file: ${path.basename(pcmFile)}`);
                    resolve();
                }
            });

            ffmpeg.on('error', (err) => {
                console.error(`FFmpeg spawn error: ${err.message}`);
                console.log(`Keeping original PCM file due to FFmpeg error: ${path.basename(pcmFile)}`);
                resolve(); // Don't reject - just keep the PCM file
            });

            // Pi timeout protection
            if (this.isRaspberryPi) {
                setTimeout(() => {
                    if (!ffmpeg.killed) {
                        console.warn('FFmpeg taking too long on Pi, terminating...');
                        ffmpeg.kill('SIGTERM');
                        resolve(); // Keep PCM file on timeout
                    }
                }, 30000); // 30 second timeout
            }
        });
    }
    async cleanup() {
        console.log('Cleaning up voice recorder...');

        // Stop all active recordings
        for (const [guildId, recording] of this.recordings) {
            try {
                console.log(`Cleaning up recording for guild ${guildId}`);

                // Stop user streams
                for (const [userId, userData] of recording.users) {
                    try {
                        userData.stream.destroy();
                    } catch (error) {
                        console.warn(`Error stopping stream for user ${userId}:`, error);
                    }
                }

                // Disconnect from voice
                recording.connection.destroy();
            } catch (error) {
                console.warn(`Error cleaning up recording for guild ${guildId}:`, error);
            }
        }

        this.recordings.clear();

        // Destroy Discord client
        if (this.client && !this.client.isReady()) {
            await this.client.destroy();
        }

        console.log('Voice recorder cleanup complete');
    }
}

// CLI interface for Python integration
if (require.main === module) {
    const [,, action, ...args] = process.argv;

    if (!process.env.DISCORD_BOT_TOKEN) {
        console.error('❌ DISCORD_BOT_TOKEN environment variable not set');
        process.exit(1);
    }

    const recorder = new VoiceRecorder(process.env.DISCORD_BOT_TOKEN);

    // Graceful shutdown handlers
    process.on('SIGINT', async () => {
        console.log('\n🛑 Received SIGINT, shutting down gracefully...');
        await recorder.cleanup();
        process.exit(0);
    });

    process.on('SIGTERM', async () => {
        console.log('\n🛑 Received SIGTERM, shutting down gracefully...');
        await recorder.cleanup();
        process.exit(0);
    });

    process.on('uncaughtException', async (error) => {
        console.error('❌ Uncaught exception:', error);
        await recorder.cleanup();
        process.exit(1);
    });

    process.on('unhandledRejection', async (reason, promise) => {
        console.error('❌ Unhandled rejection at:', promise, 'reason:', reason);
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
                console.log(`🎙️ Starting recording for guild ${guildId}`);
                const success = await recorder.startRecording(guildId, channelId, outputDir);
                if (!success) {
                    console.error('❌ Failed to start recording');
                    await recorder.cleanup();
                    process.exit(1);
                }
                console.log('✅ Recording started successfully');
                // Keep process running for recording
                break;

            case 'stop':
                const [stopGuildId] = args;
                if (!stopGuildId) {
                    console.error('Usage: node voice_recorder.js stop <guildId>');
                    process.exit(1);
                }
                console.log(`🛑 Stopping recording for guild ${stopGuildId}`);
                const stopped = await recorder.stopRecording(stopGuildId);
                if (stopped) {
                    console.log('✅ Recording stopped and processed');
                } else {
                    console.log('ℹ️ No active recording found');
                }
                await recorder.cleanup();
                process.exit(0);
                break;

            default:
                console.error('Usage: node voice_recorder.js <start|stop> [args...]');
                console.error('  start <guildId> <channelId> <outputDir> - Start recording');
                console.error('  stop <guildId> - Stop recording');
                process.exit(1);
        }
    }).catch(async error => {
        console.error('❌ Recorder initialization failed:', error);
        await recorder.cleanup();
        process.exit(1);
    });
}

module.exports = VoiceRecorder;