// test-opus.js
const { OpusEncoder } = require('@discordjs/opus');
const encoder = new OpusEncoder(48000, 2);
console.log('Opus encoder created successfully');