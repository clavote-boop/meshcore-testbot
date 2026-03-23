// mesh-hub.js — MeshCore Connection Hub v1.0
// Owns the serial connection, broadcasts messages to connected bot clients via TCP
import { NodeJSSerialConnection } from '@liamcottle/meshcore.js';
import net from 'net';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Load .env
const envPath = path.join(__dirname, '.env');
if (fs.existsSync(envPath)) {
 fs.readFileSync(envPath, 'utf8').split('\n').forEach(line => {
 const [k, ...v] = line.split('=');
 if (k && v.length) process.env[k.trim()] = v.join('=').trim();
 });
}

const SERIAL_PORT = process.env.SERIAL_PORT || '/dev/ttyUSB0';
const HUB_PORT = parseInt(process.env.HUB_PORT || '7777');
const RECONNECT_DELAY = 5000;
const VERSION = '1.0';

let connection = null;
let guzmanChannel = null;
let channels = [];
let contacts = [];
const clients = new Map(); // socket -> { name, socket }

function log(msg) {
 const ts = new Date().toISOString();
 console.log(`[hub ${ts}] ${msg}`);
}

// Broadcast a JSON message to all connected bot clients
function broadcast(obj) {
 const data = JSON.stringify(obj) + '\n';
 for (const [sock, info] of clients) {
 try { sock.write(data);
 } catch(e) { log(`Broadcast err to ${info.name}: ${e.message}`); }
 }
}

// Send a channel text message through the radio (queued)
let sendQueue = [];
let sending = false;
let fetchingMessages = false;
async function processSendQueue() {
 if (sending || sendQueue.length === 0) return;
 sending = true;
 while (sendQueue.length > 0) {
 const job = sendQueue.shift();
 try {
 if (!connection) throw new Error('No connection');
 await connection.sendChannelTextMessage(job.channelIdx, job.text);
 log(`Sent ch=${job.channelIdx}: ${job.text.slice(0,60)}...`);
 // Small delay between sends to avoid radio contention
 await new Promise(r => setTimeout(r, 1500));
 } catch(e) {
 log(`Send error: ${e.message}`);
 }
 }
 sending = false;
}

function enqueueSend(channelIdx, text) {
 sendQueue.push({ channelIdx, text });
 processSendQueue();
}

// Poll for new messages
async function pollMessages() {
  if (fetchingMessages) { setTimeout(pollMessages, 30000); return; }
  fetchingMessages = true;
 if (!connection) return;
 try {
 const waiting = await connection.getWaitingMessages();
 for (const msg of waiting) {
 if (msg.channelMessage) {
 const cm = msg.channelMessage;
 const payload = {
 type: 'channel_message',
 channelIdx: cm.channelIdx,
 senderName: cm.senderName || '',
 text: cm.text || '',
 pathLen: cm.pathLen,
 pubKeyPrefix: cm.pubKeyPrefix ? Buffer.from(cm.pubKeyPrefix).toString('hex') : '',
 timestamp: Date.now()
 };
 log(`CH msg from ${payload.senderName}: ${payload.text.slice(0,80)}`);
 broadcast(payload);
 }
 if (msg.contactMessage) {
 const dm = msg.contactMessage;
 const payload = {
 type: 'contact_message',
 senderName: dm.senderName || '',
 text: dm.text || '',
 pathLen: dm.pathLen,
 pubKeyPrefix: dm.pubKeyPrefix ? Buffer.from(dm.pubKeyPrefix).toString('hex') : '',
 timestamp: Date.now()
 };
 log(`DM from ${payload.senderName}: ${payload.text.slice(0,80)}`);
 broadcast(payload);
 }
 }
 } catch(e) {
 if (!e.message?.includes('timed out')) log(`Poll error: ${e.message}`);
 }
 fetchingMessages = false;

 // Poll again
 setTimeout(pollMessages, 30000);
}

// Refresh contacts cache and broadcast
async function refreshContacts() {
 if (!connection) return;
 try {
 contacts = await connection.getContacts();
 const contactList = contacts.map(c => ({
 name: c.advName,
 publicKey: c.publicKey ? Buffer.from(c.publicKey).toString('hex') : '',
 lat: c.advLat / 1e6,
 lon: c.advLon / 1e6,
 lastSeen: c.lastSeen
 }));
 broadcast({ type: 'contacts_update', contacts: contactList });
 log(`Contacts refreshed: ${contacts.length} nodes`);
 } catch(e) {
 log(`Contacts refresh error: ${e.message}`);
 }
}

// Refresh channels and broadcast
async function refreshChannels() {
 if (!connection) return;
 try {
 channels = await connection.getChannels();
 const chanList = channels.map(c => ({
 channelIdx: c.channelIdx,
 name: c.name
 }));
 broadcast({ type: 'channels_update', channels: chanList });
 log(`Channels refreshed: ${channels.length}`);
 } catch(e) {
 log(`Channels refresh error: ${e.message}`);
 }
}

// Handle commands from bot clients
function handleClientMessage(sock, info, line) {
 try {
 const cmd = JSON.parse(line);
 switch(cmd.action) {
 case 'register':
 info.name = cmd.name || 'unnamed';
 log(`Client registered: ${info.name}`);
 // Send current state
 sock.write(JSON.stringify({ type: 'hub_state', connected: !!connection, channels: channels.map(c => ({ channelIdx: c.channelIdx, name: c.name })) }) + '\n');
 break;
 case 'send_channel':
 if (cmd.channelIdx !== undefined && cmd.text) {
 log(`Send req from ${info.name}: ch=${cmd.channelIdx} "${cmd.text.slice(0,60)}"`);
 enqueueSend(cmd.channelIdx, cmd.text);
 }
 break;
 case 'get_contacts':
 refreshContacts();
 break;
 case 'get_channels':
 refreshChannels();
 break;
 case 'send_advert':
 if (connection) connection.sendFloodAdvert().catch(e => log(`Advert err: ${e.message}`));
 break;
 default:
 log(`Unknown action from ${info.name}: ${cmd.action}`);
 }
 } catch(e) {
 log(`Bad msg from ${info.name}: ${e.message}`);
 }
}

// Start TCP server for bot clients
const server = net.createServer((sock) => {
 const info = { name: 'new', socket: sock };
 clients.set(sock, info);
 log(`Client connected (${clients.size} total)`);

 let buffer = '';
 sock.on('data', (data) => {
 buffer += data.toString();
 let idx;
 while ((idx = buffer.indexOf('\n')) !== -1) {
 const line = buffer.slice(0, idx).trim();
 buffer = buffer.slice(idx + 1);
 if (line) handleClientMessage(sock, info, line);
 }
 });

 sock.on('close', () => {
 clients.delete(sock);
 log(`Client ${info.name} disconnected (${clients.size} total)`);
 });

 sock.on('error', (e) => {
 log(`Client ${info.name} error: ${e.message}`);
 clients.delete(sock);
 });
});

server.listen(HUB_PORT, '127.0.0.1', () => {
 log(`Hub TCP server listening on 127.0.0.1:${HUB_PORT}`);
});

// Connect to radio
function connectRadio() {
 log(`Connecting to ${SERIAL_PORT}...`);
 const conn = new NodeJSSerialConnection(SERIAL_PORT);

 conn.on('connected', async () => {
 connection = conn;
 log('Connected to MeshCore device');
 broadcast({ type: 'hub_connected' });

 try {
 // Get channels
 await refreshChannels();
 await refreshContacts();
 // Start polling
 pollMessages();
 // Listen for real-time push notifications from radio
 connection.on(0x83, async () => { // PushCodes.MsgWaiting
 log("Push: MsgWaiting received, fetching messages...");
  if (fetchingMessages) { log('Push: skipped, fetch in progress'); return; }
  fetchingMessages = true;
 try {
 const waiting = await connection.getWaitingMessages();
 for (const msg of waiting) {
 if (msg.channelMessage) {
 const cm = msg.channelMessage;
 const payload = {
 type: "channel_message",
 channelIdx: cm.channelIdx,
 senderName: cm.senderName || "",
 text: cm.text || "",
 pathLen: cm.pathLen,
 pubKeyPrefix: cm.pubKeyPrefix ? Buffer.from(cm.pubKeyPrefix).toString("hex") : "",
 timestamp: Date.now()
 };
 log(`CH msg from ${payload.senderName}: ${payload.text.slice(0,80)}`);
 broadcast(payload);
 }
 if (msg.contactMessage) {
 const dm = msg.contactMessage;
 const payload = {
 type: "contact_message",
 senderName: dm.senderName || "",
 text: dm.text || "",
 pathLen: dm.pathLen,
 pubKeyPrefix: dm.pubKeyPrefix ? Buffer.from(dm.pubKeyPrefix).toString("hex") : "",
 timestamp: Date.now()
 };
 log(`DM from ${payload.senderName}: ${payload.text.slice(0,80)}`);
 broadcast(payload);
 }
 }
 } catch(e) {
 if (!e.message?.includes("timed out")) log(`Push handler error: ${e.message}`);
 } finally { fetchingMessages = false; }
 });
 log("Push listener for MsgWaiting registered");
 // Periodic contacts refresh
 setInterval(() => refreshContacts(), 5 * 60 * 1000);
 } catch(e) {
 log(`Post-connect error: ${e.message}`);
 }
 });

 conn.on('disconnected', () => {
 log('Disconnected from device, reconnecting...');
 connection = null;
 broadcast({ type: 'hub_disconnected' });
 setTimeout(connectRadio, RECONNECT_DELAY);
 });

 conn.connect().catch(e => {
 log(`Connect failed: ${e.message}`);
 connection = null;
 setTimeout(connectRadio, RECONNECT_DELAY);
 });
}

// Graceful shutdown
process.on('SIGINT', () => {
 log('Shutting down...');
 if (connection) connection.close();
 server.close();
 process.exit(0);
});

process.on('SIGTERM', () => {
 log('SIGTERM received, shutting down...');
 if (connection) connection.close();
 server.close();
 process.exit(0);
});

log(`MeshCore Hub v${VERSION} starting...`);
log(`Serial: ${SERIAL_PORT}`);
log(`Hub port: ${HUB_PORT}`);
connectRadio();