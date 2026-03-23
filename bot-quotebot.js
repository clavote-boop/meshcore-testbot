// bot-quotebot.js – Quotebot plugin for mesh-hub
// Responds to !quote, !q, quotebot commands on channels
import HubClient from './hub-client.js';
import { getQuoteForUser, startQuoteEngine, getQuotePoolSize, getUserSeenCount } from './quote_engine.js';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const envPath = path.join(__dirname, '.env');
if (fs.existsSync(envPath)) {
 fs.readFileSync(envPath, 'utf8').split('\n').forEach(line => {
 const [k, ...v] = line.split('=');
 if (k && v.length) process.env[k.trim()] = v.join('=').trim();
 });
}

const MY_NODE_NAME = 'Clem Heavyside';
const MAX_MSG_BYTES = 190;
const GUZMAN_SECRET = '9cd8fcf22a47333b591d96a2b848b73f';

const hub = new HubClient('quotebot');
let guzmanChannelIdx = null;

// Split text into chunks that fit MAX_MSG_BYTES, up to maxParts messages
function splitMessage(text, maxParts = 3) {
 const chunks = [];
 let remaining = text;
 for (let i = 0; i < maxParts && remaining.length > 0; i++) {
 const buf = Buffer.from(remaining, 'utf8');
 if (buf.length <= MAX_MSG_BYTES) {
 chunks.push(remaining);
 remaining = '';
 } else {
 const slice = buf.slice(0, MAX_MSG_BYTES).toString('utf8');
 const lastSpace = slice.lastIndexOf(' ');
 const cutAt = lastSpace > MAX_MSG_BYTES * 0.4 ? lastSpace : MAX_MSG_BYTES;
 const cutBuf = Buffer.from(slice.substring(0, cutAt >= slice.length ? slice.length : cutAt), 'utf8');
 chunks.push(cutBuf.toString('utf8'));
 remaining = buf.slice(cutBuf.length).toString('utf8').trimStart();
 }
 }
 return chunks;
}

hub.on('hub_state', (state) => {
 hub.log(`Hub state: connected=${state.connected}, channels=${state.channels?.length || 0}`);
 if (state.channels) {
 for (const ch of state.channels) {
 hub.log(` Channel ${ch.channelIdx}: ${ch.name}`);
 }
 }
});

hub.on('channel_message', (msg) => {
 // Skip our own messages
 if (msg.senderName === MY_NODE_NAME) return;

 const text = (msg.text || '').trim();
 const lower = text.toLowerCase();

 // Respond to !quote, !q, or "quotebot" prefix
 const isQuoteCmd = lower.includes('!quote') || lower.includes('!q ') || lower.endsWith('!q') || lower.includes('quotebot');

 if (!isQuoteCmd) return;

 // Extract requester name from channel text (format: "SenderName: command")
 const colonIdx = text.indexOf(': ');
 const requesterName = colonIdx > 0 ? text.substring(0, colonIdx) : (msg.senderName || 'anon');

 hub.log(`Quote request from ${requesterName} on ch=${msg.channelIdx}: ${text}`);

 try {
 const quote = getQuoteForUser(requesterName);
 if (quote) {
 // Build full quote: @name: "quote" –Author
 const pathMi = msg.pathLen ? (msg.pathLen * 0.621371).toFixed(1) : null;
 const distStr = pathMi ? ` (${pathMi} mi)` : '';
 const fullQuote = `@${requesterName}: ${quote.q} –${quote.a}${distStr}`;

 // Split into chunks up to 3 messages
 const chunks = splitMessage(fullQuote, 3);

 hub.log(`Reply (${chunks.length} parts): ${chunks[0].substring(0, 60)}...`);

 // Send chunks with delay between them
 for (let i = 0; i < chunks.length; i++) {
 if (i === 0) {
 hub.sendChannelMessage(msg.channelIdx, chunks[i]);
 } else {
 const chunk = chunks[i];
 const chIdx = msg.channelIdx;
 setTimeout(() => hub.sendChannelMessage(chIdx, chunk), i * 2000);
 }
 }
 } else {
 hub.sendChannelMessage(msg.channelIdx, `@${requesterName}: No quotes available right now.`);
 }
 } catch(e) {
 hub.log(`Quote error: ${e.message}`);
 hub.sendChannelMessage(msg.channelIdx, 'Quote engine error.');
 }
});

// Start quote engine and connect
hub.log('Quotebot starting...');
startQuoteEngine();
hub.log(`Quote pool size: ${getQuotePoolSize()}`);
hub.connect();

process.on('SIGINT', () => { hub.close(); process.exit(0); });
process.on('SIGTERM', () => { hub.close(); process.exit(0); });
