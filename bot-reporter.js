// bot-reporter.js — Traffic reporter plugin for mesh-hub
// Tracks mesh traffic, sends periodic Telegram reports
import HubClient from './hub-client.js';
import https from 'https';
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

const TG_TOKEN = process.env.TELEGRAM_BOT_TOKEN || '';
const TG_CHAT = process.env.TELEGRAM_CHAT_ID || '';
const DATA_FILE = path.join(__dirname, 'mesh_data.json');
const HOURLY_MS = 60 * 60 * 1000;
const DAILY_MS = 24 * HOURLY_MS;

const hub = new HubClient('reporter');

const meshTraffic = {
 messages: [],
 nodesSeen: new Map(),
 quotebotRequests: []
};

function loadData() {
 try {
 if (fs.existsSync(DATA_FILE)) {
 const raw = fs.readFileSync(DATA_FILE, 'utf8');
 const data = JSON.parse(raw);
 meshTraffic.messages = data.messages || [];
 meshTraffic.nodesSeen = new Map(data.nodesSeen || []);
 meshTraffic.quotebotRequests = data.quotebotRequests || [];
 }
 } catch(e) { hub.log('Failed to load data: ' + e.message); }
}

function saveData() {
 try {
 fs.writeFileSync(DATA_FILE, JSON.stringify({
 messages: meshTraffic.messages,
 nodesSeen: Array.from(meshTraffic.nodesSeen.entries()),
 quotebotRequests: meshTraffic.quotebotRequests
 }, null, 2));
 } catch(e) { hub.log('Failed to save data: ' + e.message); }
}

function sendTelegram(text) {
 if (!TG_TOKEN || !TG_CHAT) return;
 const payload = JSON.stringify({ chat_id: TG_CHAT, text: text, parse_mode: 'HTML' });
 const options = {
 hostname: 'api.telegram.org',
 port: 443,
 path: '/bot' + TG_TOKEN + '/sendMessage',
 method: 'POST',
 headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) }
 };
 const req = https.request(options);
 req.on('error', (e) => hub.log('TG error: ' + e.message));
 req.write(payload);
 req.end();
}

function trackMessage(sender, chIdx, text) {
 const now = Date.now();
 meshTraffic.messages.push({ timestamp: now, sender, chIdx, text });
 const node = meshTraffic.nodesSeen.get(sender) || { count: 0, lastSeen: 0 };
 node.count += 1;
 node.lastSeen = now;
 meshTraffic.nodesSeen.set(sender, node);
}

function getStats() {
 const now = Date.now();
 const oneHourAgo = now - HOURLY_MS;
 const dayAgo = now - DAILY_MS;
 let msgs1h = 0, msgs24h = 0;
 const channelsSet = new Set();
 meshTraffic.messages.forEach(m => {
 if (m.timestamp >= oneHourAgo) { msgs1h++; channelsSet.add(m.chIdx); }
 if (m.timestamp >= dayAgo) msgs24h++;
 });
 const nodesActive1h = Array.from(meshTraffic.nodesSeen.values()).filter(n => n.lastSeen >= oneHourAgo).length;
 const nodesActive24h = Array.from(meshTraffic.nodesSeen.values()).filter(n => n.lastSeen >= dayAgo).length;
 return { msgs1h, msgs24h, nodesActive1h, nodesActive24h, totalNodes: meshTraffic.nodesSeen.size, totalMsgs: meshTraffic.messages.length };
}

function sendReport() {
 const stats = getStats();
 let report = '<b>MeshBot Report</b>\n';
 report += '📊 TRAFFIC:\n';
 report += 'Last 1h: ' + stats.msgs1h + ' msgs from ' + stats.nodesActive1h + ' nodes\n';
 report += 'Last 24h: ' + stats.msgs24h + ' msgs from ' + stats.nodesActive24h + ' nodes\n';
 report += 'All time: ' + stats.totalMsgs + ' msgs | ' + stats.totalNodes + ' nodes seen\n';
 report += '✅ Network health: OK\n';
 report += '\n📡 Mesh Map: https://livemap.wcmesh.com/\n';
 report += 'de Clem 73';
 sendTelegram(report);
 hub.log('Sent periodic report to Telegram');
 saveData();
}

hub.on('channel_message', (msg) => {
 trackMessage(msg.senderName, msg.channelIdx, msg.text);
 // Auto-save every 50 messages
 if (meshTraffic.messages.length % 50 === 0) saveData();
});

hub.on('contact_message', (msg) => {
 trackMessage(msg.senderName, 'DM', msg.text);
});

// Periodic reports
setInterval(sendReport, HOURLY_MS);

// Startup
hub.log('Reporter starting...');
loadData();
hub.connect();

hub.on('hub_connected', () => {
 sendTelegram('<b>MeshBot Hub</b> connected 🟢');
});

hub.on('hub_disconnected', () => {
 sendTelegram('<b>MeshBot Hub</b> disconnected 🔴');
});

process.on('SIGINT', () => { saveData(); hub.close(); process.exit(0); });
process.on('SIGTERM', () => { saveData(); hub.close(); process.exit(0); });
