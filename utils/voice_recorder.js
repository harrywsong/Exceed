const { Client, GatewayIntentBits } = require('discord.js');
const { joinVoiceChannel, VoiceConnectionStatus, EndBehaviorType } = require('@discordjs/voice');
const prism = require('prism-media');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

class VoiceRecorder {
    constructor(token) {
        this.client = new Client({
            intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates]
        });
        this.token = token;
        this.recordings = new Map();
        this.isRaspberryPi = this.detectRaspberryPi();
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
        await this.client.login(this.token);
        console.log(`Voice recorder logged in as ${this.client.user.tag}`);
        if (this.isRaspberryPi) {
            console.log('🥧 Raspberry Pi detected - using optimized settings');
        }
    }

    async startRecording(guildId, channelId, outputDir) {
        try {
            const guild = this.client.guilds.cache.get(guildId);
            if (!guild) {
                console.error(`Guild ${guildId} not found`);
                return false;
            }

            const channel = guild.channels.cache.get(channelId);
            if (!channel) {
                console.error(`Channel ${channelId} not found`);
                return false;
            }

            console.log(`Starting recording in ${channel.name}`);

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
                startTime: Date.now()
            };

            connection.on(VoiceConnectionStatus.Ready, () => {
                console.log('Voice connection ready, setting up recording');
                this.setupUserRecording(connection, recording);
            });

            connection.on(VoiceConnectionStatus.Disconnected, () => {
                console.log('Voice connection disconnected');
            });

            connection.on('error', (error) => {
                console.error('Voice connection error:', error);
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

            const ffmpeg = spawn('ffmpeg', ffmpegArgs);

            let stderr = '';
            ffmpeg.stderr.on('data', (data) => {
                stderr += data.toString();
            });

            ffmpeg.on('close', (code) => {
                if (code === 0) {
                    try {
                        fs.unlinkSync(pcmFile);
                        console.log(`✅ Successfully converted ${path.basename(pcmFile)}`);
                        resolve();
                    } catch (err) {
                        console.warn(`Could not delete PCM file: ${err.message}`);
                        resolve();
                    }
                } else {
                    console.error(`❌ FFmpeg failed with code ${code}:`);
                    if (stderr) console.error(stderr);
                    reject(new Error(`FFmpeg exited with code ${code}`));
                }
            });

            ffmpeg.on('error', (err) => {
                console.error(`FFmpeg spawn error: ${err.message}`);
                reject(err);
            });

            // Pi timeout protection
            if (this.isRaspberryPi) {
                setTimeout(() => {
                    if (!ffmpeg.killed) {
                        console.warn('FFmpeg taking too long on Pi, terminating...');
                        ffmpeg.kill('SIGTERM');
                        reject(new Error('FFmpeg timeout on Pi'));
                    }
                }, 30000); // 30 second timeout
            }
        });
    }

    async cleanup() {
        for (const [guildId, recording] of this.recordings) {
            try {
                recording.connection.destroy();
            } catch {}
        }
        this.client.destroy();
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
    }).catch(error => {
        console.error('❌ Recorder initialization failed:', error);
        process.exit(1);
    });
}

module.exports = VoiceRecorder;