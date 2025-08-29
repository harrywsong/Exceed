const { Client, GatewayIntentBits } = require('discord.js');
const {
    joinVoiceChannel,
    VoiceConnectionStatus,
    EndBehaviorType,
    entersState
} = require('@discordjs/voice');
const prism = require('prism-media');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

class VoiceRecorder {
    constructor(token) {
        this.client = new Client({
            intents: [
                GatewayIntentBits.Guilds,
                GatewayIntentBits.GuildVoiceStates,
                GatewayIntentBits.GuildMessages
            ]
        });
        this.token = token;
        this.recordings = new Map();
        this.isRaspberryPi = this.detectRaspberryPi();
        this.isReady = false;
    }

    detectRaspberryPi() {
        try {
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
            this.client.once('ready', () => {
                console.log(`Voice recorder logged in as ${this.client.user.tag}`);
                if (this.isRaspberryPi) {
                    console.log('Raspberry Pi detected - using optimized settings');
                }

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

            this.client.login(this.token).catch(reject);
        });
    }

    async startRecording(guildId, channelId, outputDir) {
        try {
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
                        .filter(c => c.type === 2)
                        .map(c => `${c.name} (${c.id})`)
                );
                return false;
            }

            if (channel.type !== 2) {
                console.error(`Channel ${channel.name} is not a voice channel (type: ${channel.type})`);
                return false;
            }

            console.log(`Starting recording in voice channel: ${channel.name}`);

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
                    duration: this.isRaspberryPi ? 200 : 100,
                },
            });

            const filename = path.join(recording.outputDir, `${userId}.wav`);

            // Use FFmpeg to decode Opus directly to WAV
            let ffmpegArgs = [
                '-f', 'opus',            // Input format: Opus packets
                '-i', 'pipe:0',          // Input from stdin (piped from userStream)
                '-acodec', 'pcm_s16le',  // Output codec: signed 16-bit PCM
                '-f', 'wav',             // Output container: WAV
                '-ar', '48000',          // Sample rate: 48kHz
                '-ac', '2',              // Stereo channels
                '-y',                    // Overwrite if exists
                filename                 // Output file
            ];

            // Raspberry Pi optimizations: limit threads
            if (this.isRaspberryPi) {
                const outputIndex = ffmpegArgs.indexOf(filename);
                ffmpegArgs.splice(outputIndex, 0, '-threads', '1');
            }

            console.log(`Starting FFmpeg decoder for user ${userId}: ffmpeg ${ffmpegArgs.join(' ')}`);

            const ffmpegProcess = spawn('/usr/bin/ffmpeg', ffmpegArgs, {
                stdio: ['pipe', 'pipe', 'pipe']  // stdin, stdout, stderr
            });

            // Pipe Opus stream to FFmpeg stdin
            userStream.pipe(ffmpegProcess.stdin);

            // Log FFmpeg output for debugging
            let stdout = '';
            let stderr = '';
            ffmpegProcess.stdout.on('data', (data) => { stdout += data.toString(); });
            ffmpegProcess.stderr.on('data', (data) => { stderr += data.toString(); });

            ffmpegProcess.on('close', (code) => {
                console.log(`FFmpeg for user ${userId} exited with code ${code}`);
                if (code !== 0) {
                    console.error(`FFmpeg stdout: ${stdout.trim()}`);
                    console.error(`FFmpeg stderr: ${stderr.trim()}`);
                }
                // Check if WAV file is valid
                if (fs.existsSync(filename)) {
                    const stats = fs.statSync(filename);
                    if (stats.size <= 44) {  // Empty WAV (just header)
                        console.error(`Empty/invalid WAV for ${userId}, removing it`);
                        fs.unlinkSync(filename);
                    }
                }
            });

            ffmpegProcess.on('error', (err) => {
                console.error(`FFmpeg error for user ${userId}: ${err.message}`);
            });

            recording.users.set(userId, {
                stream: userStream,
                ffmpeg: ffmpegProcess,
                file: filename,
                startTime: Date.now(),
                channels: 2  // Fixed stereo
            });

            userStream.on('end', () => {
                console.log(`User ${userId} stopped speaking`);
                if (ffmpegProcess && !ffmpegProcess.killed) {
                    ffmpegProcess.stdin.end();
                }
            });

            userStream.on('error', (error) => {
                console.error(`Stream error for user ${userId}:`, error);
                if (ffmpegProcess && !ffmpegProcess.killed) {
                    ffmpegProcess.kill('SIGTERM');
                }
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

        // Stop all user streams and FFmpeg processes gracefully
        for (const [userId, userData] of recording.users) {
            try {
                userData.stream.destroy();
                if (userData.ffmpeg && !userData.ffmpeg.killed) {
                    userData.ffmpeg.stdin.end();
                    await new Promise(resolve => setTimeout(resolve, 1000));  // Brief wait for flush
                    userData.ffmpeg.kill('SIGTERM');
                }
            } catch (error) {
                console.warn(`Error stopping stream/FFmpeg for user ${userId}:`, error);
            }
        }

        // Wait longer on Pi for file writes to complete
        await new Promise(resolve => setTimeout(resolve, this.isRaspberryPi ? 15000 : 5000));

        // Clean up connection
        try {
            recording.connection.destroy();
        } catch (error) {
            console.warn('Error destroying voice connection:', error);
        }

        this.recordings.delete(guildId);
        return true;
    }
    async processRecording(recording) {
        console.log('Processing recorded audio files');
        console.log(`Found ${recording.users.size} user streams to process`);

        const processPromises = [];

        for (const [userId, userData] of recording.users) {
            const pcmFile = userData.file;
            const wavFile = pcmFile.replace('.pcm', '.wav');

            console.log(`Checking PCM file: ${pcmFile}`);

            if (fs.existsSync(pcmFile)) {
                const stats = fs.statSync(pcmFile);
                console.log(`PCM file ${path.basename(pcmFile)}: ${stats.size} bytes`);

                if (stats.size > 0) {
                    console.log(`Queuing conversion: ${path.basename(pcmFile)} -> ${path.basename(wavFile)} (${userData.channels} channels)`);

                    if (this.isRaspberryPi) {
                        // Process files sequentially on Pi to avoid overload
                        await this.convertPcmToWav(pcmFile, wavFile, userData.channels);
                    } else {
                        processPromises.push(this.convertPcmToWav(pcmFile, wavFile, userData.channels));
                    }
                } else {
                    console.log(`Skipping empty PCM file for user ${userId}`);
                    try {
                        fs.unlinkSync(pcmFile);
                        console.log(`Deleted empty PCM file: ${path.basename(pcmFile)}`);
                    } catch (e) {
                        console.warn(`Could not delete empty PCM file: ${e.message}`);
                    }
                }
            } else {
                console.warn(`PCM file ${pcmFile} does not exist - may have been deleted or never created`);
            }
        }

        if (!this.isRaspberryPi && processPromises.length > 0) {
            try {
                console.log(`Processing ${processPromises.length} conversions in parallel`);
                await Promise.all(processPromises);
                console.log('All audio files processed successfully');
            } catch (error) {
                console.error('Error processing audio files:', error);
            }
        }

        // Final summary
        console.log('Processing complete. Final file listing:');
        if (fs.existsSync(recording.outputDir)) {
            const finalFiles = fs.readdirSync(recording.outputDir);
            finalFiles.forEach(file => {
                const filePath = path.join(recording.outputDir, file);
                const stats = fs.statSync(filePath);
                console.log(`  ${file}: ${stats.size} bytes`);
            });
        } else {
            console.log('  Output directory no longer exists');
        }
    }

    async checkFFmpegAvailability() {
        return new Promise((resolve) => {
            const ffmpegCheck = spawn('/usr/bin/ffmpeg', ['-version']);

            ffmpegCheck.on('close', (code) => {
                if (code === 0) {
                    console.log('FFmpeg is available');
                    resolve(true);
                } else {
                    console.error('FFmpeg is not available or not working properly');
                    resolve(false);
                }
            });

            ffmpegCheck.on('error', (err) => {
                console.error('FFmpeg not found:', err.message);
                resolve(false);
            });
        });
    }

    async convertPcmToWav(pcmFile, wavFile, channels = 2) {
        // Check FFmpeg availability first
        const ffmpegAvailable = await this.checkFFmpegAvailability();
        if (!ffmpegAvailable) {
            console.error('FFmpeg not available, keeping PCM file');
            return;
        }

        return new Promise((resolve, reject) => {
            console.log(`Converting ${path.basename(pcmFile)} to ${path.basename(wavFile)} (${channels} channels)`);

            // Verify PCM file exists and has content
            if (!fs.existsSync(pcmFile)) {
                console.log(`PCM file ${pcmFile} does not exist, skipping conversion`);
                resolve();
                return;
            }

            const stats = fs.statSync(pcmFile);
            if (stats.size === 0) {
                console.log(`PCM file ${pcmFile} is empty, removing it`);
                try {
                    fs.unlinkSync(pcmFile);
                } catch (e) {
                    console.warn(`Could not delete empty PCM file: ${e.message}`);
                }
                resolve();
                return;
            }

            console.log(`PCM file size: ${stats.size} bytes`);

            // Build FFmpeg command with proper settings
            const ffmpegArgs = [
                '-f', 's16le',           // Input format: signed 16-bit little endian
                '-ar', '48000',          // Sample rate: 48kHz
                '-ac', channels.toString(), // Audio channels
                '-i', pcmFile,           // Input file
                '-acodec', 'pcm_s16le',  // Output codec
                '-ar', '48000',          // Ensure output sample rate
                '-y',                    // Overwrite output file
                wavFile                  // Output file
            ];

            // Raspberry Pi optimizations
            if (this.isRaspberryPi) {
                // Insert optimization flags before the output file
                const outputIndex = ffmpegArgs.indexOf(wavFile);
                ffmpegArgs.splice(outputIndex, 0, '-threads', '2');
            }

            console.log(`Running FFmpeg: ffmpeg ${ffmpegArgs.join(' ')}`);

            const ffmpeg = spawn('/usr/bin/ffmpeg', ffmpegArgs, {
                stdio: ['ignore', 'pipe', 'pipe']
            });

            let stdout = '';
            let stderr = '';

            ffmpeg.stdout.on('data', (data) => {
                stdout += data.toString();
            });

            ffmpeg.stderr.on('data', (data) => {
                stderr += data.toString();
            });

            // Set up timeout for Raspberry Pi
            const timeout = this.isRaspberryPi ? 60000 : 30000;
            const timeoutHandle = setTimeout(() => {
                if (!ffmpeg.killed) {
                    console.warn(`FFmpeg timeout after ${timeout/1000}s for ${path.basename(pcmFile)}, killing process...`);
                    ffmpeg.kill('SIGKILL');
                }
            }, timeout);

            // Handle process completion
            ffmpeg.on('close', (code) => {
                clearTimeout(timeoutHandle);
                console.log(`FFmpeg process exited with code ${code}`);

                if (code === 0) {
                    // Verify WAV file was created successfully
                    if (fs.existsSync(wavFile)) {
                        const wavStats = fs.statSync(wavFile);
                        console.log(`WAV file created: ${wavStats.size} bytes`);

                        if (wavStats.size > 44) { // WAV header is 44 bytes minimum
                            console.log(`Successfully converted ${path.basename(pcmFile)} -> ${path.basename(wavFile)}`);

                            // Delete PCM file only after successful conversion
                            try {
                                fs.unlinkSync(pcmFile);
                                console.log(`Deleted original PCM file: ${path.basename(pcmFile)}`);
                            } catch (err) {
                                console.warn(`Could not delete PCM file: ${err.message}`);
                            }
                            resolve();
                        } else {
                            console.error(`WAV file seems invalid (${wavStats.size} bytes), keeping PCM`);
                            try {
                                fs.unlinkSync(wavFile);
                            } catch (e) {
                                console.warn(`Could not delete invalid WAV file: ${e.message}`);
                            }
                            resolve();
                        }
                    } else {
                        console.error('FFmpeg reported success but WAV file not found, keeping PCM');
                        resolve();
                    }
                } else {
                    console.error(`FFmpeg failed with exit code ${code}`);
                    if (stderr) {
                        console.error(`FFmpeg stderr: ${stderr.trim()}`);
                    }
                    if (stdout) {
                        console.log(`FFmpeg stdout: ${stdout.trim()}`);
                    }
                    console.log(`Keeping original PCM file: ${path.basename(pcmFile)}`);
                    resolve(); // Don't reject - just keep the PCM file
                }
            });

            // Handle spawn errors
            ffmpeg.on('error', (err) => {
                clearTimeout(timeoutHandle);
                console.error(`FFmpeg spawn error: ${err.message}`);
                console.log(`Keeping original PCM file due to spawn error: ${path.basename(pcmFile)}`);
                resolve(); // Don't reject - just keep the PCM file
            });
        });
    }
    async cleanup() {
        console.log('Cleaning up voice recorder...');

        for (const [guildId, recording] of this.recordings) {
            try {
                console.log(`Cleaning up recording for guild ${guildId}`);

                for (const [userId, userData] of recording.users) {
                    try {
                        userData.stream.destroy();
                    } catch (error) {
                        console.warn(`Error stopping stream for user ${userId}:`, error);
                    }
                }

                recording.connection.destroy();
            } catch (error) {
                console.warn(`Error cleaning up recording for guild ${guildId}:`, error);
            }
        }

        this.recordings.clear();

        if (this.client && this.client.isReady()) {
            await this.client.destroy();
        }

        console.log('Voice recorder cleanup complete');
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
    process.on('SIGINT', async () => {
        console.log('\nReceived SIGINT, shutting down gracefully...');
        await recorder.cleanup();
        process.exit(0);
    });

    process.on('SIGTERM', async () => {
        console.log('\nReceived SIGTERM, shutting down gracefully...');
        await recorder.cleanup();
        process.exit(0);
    });

    process.on('uncaughtException', async (error) => {
        console.error('Uncaught exception:', error);
        await recorder.cleanup();
        process.exit(1);
    });

    process.on('unhandledRejection', async (reason, promise) => {
        console.error('Unhandled rejection at:', promise, 'reason:', reason);
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
                console.log(`Starting recording for guild ${guildId}`);
                const success = await recorder.startRecording(guildId, channelId, outputDir);
                if (!success) {
                    console.error('Failed to start recording');
                    await recorder.cleanup();
                    process.exit(1);
                }
                console.log('Recording started successfully');
                break;

            case 'stop':
                const [stopGuildId] = args;
                if (!stopGuildId) {
                    console.error('Usage: node voice_recorder.js stop <guildId>');
                    process.exit(1);
                }
                console.log(`Stopping recording for guild ${stopGuildId}`);
                const stopped = await recorder.stopRecording(stopGuildId);
                if (stopped) {
                    console.log('Recording stopped and processed');
                } else {
                    console.log('No active recording found');
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
        console.error('Recorder initialization failed:', error);
        await recorder.cleanup();
        process.exit(1);
    });
}

module.exports = VoiceRecorder;