// bot-quakebot.js — Quakebot plugin for mesh-hub
// Responds to quakebot commands with earthquake data from USGS
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

const hub = new HubClient('quakebot');

// Earthquake quips
const QUIPS = [
  'Stay grounded out there!',
  'That really shook things up!',
  'Rock and roll, California style!',
  'Mother Earth just stretched.',
  'Seismographs say hi!',
  'The earth moved - and it wasnt love.',
  'Plate tectonics: never a dull moment.',
  'Just another day on the Ring of Fire.',
  'The ground has opinions today.',
  'Shake it off - literally.',
  'Geology in action!',
  'The fault is not yours.',
  'Earth: still under construction.',
  'Tectonic tango!',
  'Nature reminding us who is boss.'
];

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

async function fetchQuakes(lat, lon, radiusKm = 200, hours = 24) {
  const now = new Date();
  const end = now.toISOString();
  const start = new Date(now.getTime() - hours * 3600 * 1000).toISOString();
  const url = USGS_BASE + '?format=geojson&latitude=' + lat + '&longitude=' + lon + '&maxradiuskm=' + radiusKm + '&starttime=' + start + '&endtime=' + end + '&orderby=magnitude&limit=5';
  return await httpGet(url);
}

function formatQuake(f) {
  const mag = f.properties.mag;
  const place = f.properties.place;
  const time = new Date(f.properties.time);
  const hh = time.getUTCHours().toString().padStart(2, '0');
  const mm = time.getUTCMinutes().toString().padStart(2, '0');
  return `M${mag.toFixed(1)} ${place} ${hh}:${mm}UTC`;
}

function randomQuip() {
  return QUIPS[Math.floor(Math.random() * QUIPS.length)];
}

hub.on('channel_message', async (msg) => {
  if (msg.senderName === MY_NODE_NAME) return;

  const text = (msg.text || '').trim();
  const lower = text.toLowerCase();

  if (!lower.includes('quakebot')) return;

  const colonIdx = text.indexOf(': ');
  const requesterName = colonIdx > 0 ? text.substring(0, colonIdx) : (msg.senderName || 'anon');
  let afterColon = colonIdx > 0 ? text.substring(colonIdx + 2) : text;
  const locMatch = afterColon.match(/quakebot\s+(.+)/i);
  let wlat = DEFAULT_LAT, wlon = DEFAULT_LON, wlabel = DEFAULT_LABEL;
  if (locMatch) {
    try {
      const geo = await geocodeLocation(locMatch[1].trim());
      if (geo) { wlat = geo.lat; wlon = geo.lon; wlabel = geo.name; }
    } catch (e) { hub.log(`Geocode error: ${e.message}`); }
  }

  const distMi = ((msg.pathLen || 0) * 0.621371).toFixed(1);

  try {
    const quakeData = await fetchQuakes(wlat, wlon);
    const features = quakeData.features || [];
    if (features.length === 0) {
      const reply = `@${requesterName}: Quakebot ${wlabel} - No quakes in 24h (${distMi}mi)`;
      hub.sendChannelMessage(msg.channelIdx, reply);
      // End with a random quip
      hub.sendChannelMessage(msg.channelIdx, randomQuip());
    } else {
      const header = `@${requesterName}: Quakebot ${wlabel} 24h (${distMi}mi)`;
      hub.sendChannelMessage(msg.channelIdx, header);
      const line = features.map(formatQuake).join(' | ');
      const chunks = [];
      let remaining = line;
      while (remaining.length > 0) {
        const chunk = truncate(remaining);
        chunks.push(chunk);
        remaining = remaining.slice(chunk.length);
      }
      for (const c of chunks) {
        hub.sendChannelMessage(msg.channelIdx, c);
      }
      // Determine max magnitude
      const mags = features.map(f => f.properties.mag);
      const maxMag = Math.max(...mags);
      if (maxMag < 5.5) {
        hub.sendChannelMessage(msg.channelIdx, randomQuip());
      } else if (maxMag < 7.0) {
        hub.sendChannelMessage(msg.channelIdx, 'Drop, Cover, Hold On. Check gas lines. Monitor #earthquake for updates.');
      } else {
        hub.sendChannelMessage(msg.channelIdx, 'EMERGENCY: Drop, Cover, Hold On. Move away from windows. Check for gas leaks. Do not use elevators. Monitor #earthquake for continuous updates.');
      }
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
