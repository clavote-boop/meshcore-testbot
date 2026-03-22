// bot-quotebot.js — Quotebot plugin for mesh-hub
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

function truncate(text) {
 const buf = Buffer.from(text, 'utf8');
 if (buf.length <= MAX_MSG_BYTES) return text;
 const t = buf.slice(0, MAX_MSG_BYTES - 3);
 return t.toString('utf8') + '...';
}

hub.on('hub_state', (state) => {
 hub.log(`Hub state: connected=${state.connected}, channels=${state.channels?.length || 0}`);
 // Find GUZMAN channel by matching known channels
 if (state.channels) {
 for (const ch of state.channels) {
 hub.log(` Channel ${ch.channelIdx}: ${ch.name}`);
 }
 // We'll respond on whatever channel messages come from
 }
});

hub.on('channel_message', (msg) => {
 // Skip our own messages
 if (msg.senderName === MY_NODE_NAME) return;

 const text = (msg.text || '').trim();
 const lower = text.toLowerCase();

 // Respond to !quote, !q, or "quotebot" prefix
 const isQuoteCmd = lower.startsWith('!quote') || lower.startsWith('!q ') || lower === '!q' || lower.startsWith('quotebot');

 if (!isQuoteCmd) return;

 hub.log(`Quote request from ${msg.senderName} on ch=${msg.channelIdx}: ${text}`);

 try {
 const quote = getQuoteForUser(msg.senderName);
 if (quote) {
 const reply = truncate(`${quote.q} —${quote.a}`);
 hub.log(`Reply: ${reply}`);
 hub.sendChannelMessage(msg.channelIdx, reply);
 } else {
 hub.sendChannelMessage(msg.channelIdx, 'No quotes available right now.');
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
