// bot-quakebot.js — Quakebot plugin for mesh-hub
// Responds to quakebot commands with recent earthquake data from USGS
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

const MY_NODE_NAME = 'Clem Heavyside';
const DEFAULT_LAT = process.env.DEFAULT_LAT || '37.2713';
const DEFAULT_LON = process.env.DEFAULT_LON || '-121.8366';
const DEFAULT_LABEL = 'San Jose';
const MAX_MSG_BYTES = 190;
const USGS_BASE = 'https://earthquake.usgs.gov/fdsnws/event/1/query';
const EMOJI_MAP = { green: '🟢', yellow: '🟡', orange: '🟠', red: '🔴', unknown: '⚪' };

const hub = new HubClient('quakebot');

function truncate(text) {
  const buf = Buffer.from(text, 'utf8');
  if (buf.length <= MAX_MSG_BYTES) return text;
  const t = buf.slice(0, MAX_MSG_BYTES - 3);
  return t.toString('utf8') + '...';
}

function httpGet(url) {
  return new Promise((resolve, reject) => {
    https.get(url, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => {
        try { resolve(JSON.parse(d)); } catch (e) { reject(e); }
      });
    }).on('error', reject);
  });
}

async function geocodeLocation(query) {
  const url = 'https://geocoding-api.open-meteo.com/v1/search?name=' + encodeURIComponent(query) + '&count=1&language=en&format=json';
  const j = await httpGet(url);
  if (j.results && j.results.length > 0) {
    return { lat: j.results[0].latitude, lon: j.results[0].longitude, name: j.results[0].name };
  }
  return null;
}

async function fetchQuakes(lat, lon, radiusKm = 150, hours = 24) {
  const now = new Date();
  const end = now.toISOString();
  const start = new Date(now.getTime() - hours * 3600 * 1000).toISOString();
  const url = USGS_BASE + '?format=geojson&latitude=' + lat + '&longitude=' + lon + '&maxradiuskm=' + radiusKm + '&starttime=' + start + '&endtime=' + end + '&orderby=magnitude&limit=10';
  return await httpGet(url);
}

function formatQuake(f) {
  const mag = f.properties.mag;
  const place = f.properties.place;
  const time = new Date(f.properties.time);
  const alert = f.properties.alert;
  const emoji = EMOJI_MAP[alert] || EMOJI_MAP.unknown;
  return `${emoji} M${mag.toFixed(1)} ${place} ${time.toUTCString().slice(17,22)}UTC`;
}

hub.on("message", (m) => { hub.log("DEBUG RAW msg type=" + m.type); });
hub.on('channel_message', async (msg) => {
  if (msg.senderName === MY_NODE_NAME) return;

  const text = (msg.text || '').trim();
  const lower = text.toLowerCase();
  if (!lower.startsWith('quakebot')) return;

  const colonIdx = text.indexOf(': ');
  const requesterName = colonIdx > 0 ? text.substring(0, colonIdx) : (msg.senderName || 'anon');

  hub.log(`Quake request from ${msg.senderName} on ch=${msg.channelIdx}: ${text}`);

  let lat = DEFAULT_LAT, lon = DEFAULT_LON, label = DEFAULT_LABEL;
  const query = text.slice(8).trim(); // after 'quakebot'
  if (query) {
    try {
      const geo = await geocodeLocation(query);
      if (geo) { lat = geo.lat; lon = geo.lon; label = geo.name; }
    } catch (e) { hub.log(`Geocode error: ${e.message}`); }
  }

  try {
    const quakeData = await fetchQuakes(lat, lon);
    const features = quakeData.features || [];
    const distStr = ((msg.pathLen || 0) * 0.621371).toFixed(1) + 'mi';
    if (features.length === 0) {
      const reply = `@${requesterName}: Quakebot - No quakes near ${label} in 24h (${distStr})`;
      hub.sendChannelMessage(msg.channelIdx, reply);
    } else {
      const header = `@${requesterName}: Quakebot - ${label} 24h (${distStr})`;
      hub.sendChannelMessage(msg.channelIdx, header);
      const top = features.slice(0, 5).map(formatQuake).join(' | ');
      const line2 = truncate(top);
      hub.sendChannelMessage(msg.channelIdx, line2);
    }
  } catch (e) {
    hub.log(`Quake error: ${e.message}`);
    hub.sendChannelMessage(msg.channelIdx, truncate('Quake unavailable: ' + e.message));
  }
});

hub.on('contact_message', async (msg) => {
  // Reuse same logic for direct messages
  if (msg.senderName === MY_NODE_NAME) return;
  const text = (msg.text || '').trim();
  const lower = text.toLowerCase();
  if (!lower.startsWith('quakebot')) return;

  const colonIdx = text.indexOf(': ');
  const requesterName = colonIdx > 0 ? text.substring(0, colonIdx) : (msg.senderName || 'anon');

  hub.log(`Quake DM from ${msg.senderName}: ${text}`);

  let lat = DEFAULT_LAT, lon = DEFAULT_LON, label = DEFAULT_LABEL;
  const query = text.slice(8).trim();
  if (query) {
    try {
      const geo = await geocodeLocation(query);
      if (geo) { lat = geo.lat; lon = geo.lon; label = geo.name; }
    } catch (e) { hub.log(`Geocode error: ${e.message}`); }
  }

  try {
    const quakeData = await fetchQuakes(lat, lon);
    const features = quakeData.features || [];
    const distStr = ((msg.pathLen || 0) * 0.621371).toFixed(1) + 'mi';
    if (features.length === 0) {
      const reply = `@${requesterName}: Quakebot - No quakes near ${label} in 24h (${distStr})`;
      hub.sendChannelMessage(msg.channelIdx, reply);
    } else {
      const header = `@${requesterName}: Quakebot - ${label} 24h (${distStr})`;
      hub.sendChannelMessage(msg.channelIdx, header);
      const top = features.slice(0, 5).map(formatQuake).join(' | ');
      const line2 = truncate(top);
      hub.sendChannelMessage(msg.channelIdx, line2);
    }
  } catch (e) {
    hub.log(`Quake error: ${e.message}`);
    hub.sendChannelMessage(msg.channelIdx, truncate('Quake unavailable: ' + e.message));
  }
});

hub.log('Quakebot starting...');
hub.connect();

process.on('SIGINT', () => { hub.close(); process.exit(0); });
process.on('SIGTERM', () => { hub.close(); process.exit(0); });
