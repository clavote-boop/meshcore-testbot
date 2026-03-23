// bot-quakealert.js — Continuous earthquake monitoring bot for mesh-hub
// Queries USGS for recent earthquakes and alerts appropriate channels
import HubClient from './hub-client.js';
import https from 'https';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Load optional .env file
const envPath = path.join(__dirname, '.env');
if (fs.existsSync(envPath)) {
  fs.readFileSync(envPath, 'utf8').split('\n').forEach(line => {
    const [k, ...v] = line.split('=');
    if (k && v.length) process.env[k.trim()] = v.join('=').trim();
  });
}

// Constants
const POLL_INTERVAL = 60000; // 60 seconds
const USGS_BASE = 'https://earthquake.usgs.gov/fdsnws/event/1/query';
const CALIFORNIA_LAT = 36.7783;
const CALIFORNIA_LON = -119.4179;
const CA_RADIUS_KM = 500;

const REGIONS = [
  { name: 'Bay Area', lat: 37.7749, lon: -122.4194, radiusKm: 150, channel: 7 },
  { name: 'Los Angeles', lat: 34.0522, lon: -118.2437, radiusKm: 150, channel: 8 },
  { name: 'San Diego', lat: 32.7157, lon: -117.1611, radiusKm: 100, channel: 9 },
];

const EARTHQUAKE_CHANNEL = 10; // main #earthquake channel

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

// Tracking already‑reported quake IDs
const REPORTED_IDS = new Set();
let lastReportHour = null;

function truncate(text) {
  const MAX_MSG_BYTES = 190;
  const buf = Buffer.from(text, 'utf8');
  if (buf.length <= MAX_MSG_BYTES) return text;
  const t = buf.slice(0, MAX_MSG_BYTES - 3);
  return t.toString('utf8') + '...';
}

function httpGet(url) {
  return new Promise((resolve, reject) => {
    https.get(url, (res) => {
      let data = '';
      res.on('data', (c) => { data += c; });
      res.on('end', () => {
        try { resolve(JSON.parse(data)); } catch (e) { reject(e); }
      });
    }).on('error', reject);
  });
}

function formatQuake(feature) {
  const mag = feature.properties.mag;
  const place = feature.properties.place;
  const time = new Date(feature.properties.time);
  const hh = time.getUTCHours().toString().padStart(2, '0');
  const mm = time.getUTCMinutes().toString().padStart(2, '0');
  return `M${mag.toFixed(1)} ${place} ${hh}:${mm}UTC`;
}

function randomQuip() {
  return QUIPS[Math.floor(Math.random() * QUIPS.length)];
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const toRad = deg => deg * Math.PI / 180;
  const R = 6371; // Earth radius km
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

async function fetchCaliforniaQuakes() {
  const now = new Date();
  const end = now.toISOString();
  const start = new Date(now.getTime() - 60 * 60 * 1000).toISOString(); // last 60 minutes
  const url = `${USGS_BASE}?format=geojson&latitude=${CALIFORNIA_LAT}&longitude=${CALIFORNIA_LON}&maxradiuskm=${CA_RADIUS_KM}` +
    `&starttime=${start}&endtime=${end}&limit=20&minmagnitude=2.0`;
  return await httpGet(url);
}

function getSeverityTier(mag) {
  if (mag < 4) return 1;
  if (mag < 5.5) return 2;
  if (mag < 7) return 3;
  return 4;
}

function getRegionalChannels(lat, lon) {
  const channels = [];
  for (const region of REGIONS) {
    const d = haversineKm(lat, lon, region.lat, region.lon);
    if (d <= region.radiusKm) {
      channels.push(region.channel);
    }
  }
  return channels;
}

async function alertQuake(feature) {
  const mag = feature.properties.mag;
  const tier = getSeverityTier(mag);
  const lat = feature.geometry.coordinates[1];
  const lon = feature.geometry.coordinates[0];
  const regional = getRegionalChannels(lat, lon);
  const baseMsg = `QuakeAlert: ${formatQuake(feature)}`;
  let finalMsg = '';
  const channels = [EARTHQUAKE_CHANNEL, ...regional];
  if (tier === 1) {
    finalMsg = `${baseMsg} | ${randomQuip()}`;
  } else if (tier === 2) {
    finalMsg = `${baseMsg} | ${randomQuip()}`;
  } else if (tier === 3) {
    channels.push(1); // test channel
    finalMsg = `${baseMsg} | Drop, Cover, Hold On. Check gas lines. Monitor #earthquake for updates.`;
  } else { // tier 4
    channels.push(0); // public channel, send once per quake
    finalMsg = `${baseMsg} | EMERGENCY: Drop, Cover, Hold On. Move away from windows. Check for gas leaks. Do not use elevators. Tune to #earthquake or #earthquake[region] for continuous updates.`;
  }
  for (const ch of channels) {
    hub.sendChannelMessage(ch, truncate(finalMsg));
  }
}

async function pollLoop() {
  try {
    const data = await fetchCaliforniaQuakes();
    const features = data.features || [];
    for (const f of features) {
      const id = f.id;
      if (REPORTED_IDS.has(id)) continue;
      await alertQuake(f);
      REPORTED_IDS.add(id);
    }
    // prune if too many IDs
    if (REPORTED_IDS.size > 1000) {
      const ids = Array.from(REPORTED_IDS);
      const removeCount = Math.floor(ids.length / 2);
      for (let i = 0; i < removeCount; i++) {
        REPORTED_IDS.delete(ids[i]);
      }
    }
    await scheduledReport();
  } catch (e) {
    hub.log(`Poll error: ${e.message}`);
  }
}

async function scheduledReport() {
  const now = new Date();
  const hour = now.getUTCHours(); // 0-23 UTC
  if ((hour === 13 || hour === 1) && lastReportHour !== hour) {
    // 6 am/pm PDT (UTC‑7)
    const end = now.toISOString();
    const start = new Date(now.getTime() - 12 * 3600 * 1000).toISOString();
    const url = `${USGS_BASE}?format=geojson&latitude=${CALIFORNIA_LAT}&longitude=${CALIFORNIA_LON}&maxradiuskm=${CA_RADIUS_KM}` +
      `&starttime=${start}&endtime=${end}&limit=50&minmagnitude=1.0&orderby=magnitude`;
    const data = await httpGet(url);
    const features = data.features || [];
    const period = hour === 13 ? 'AM' : 'PM';
    const header = `Quakebot CA Report ${period}`;
    hub.sendChannelMessage(EARTHQUAKE_CHANNEL, header);
    const top = features.slice(0, 10);
    for (const f of top) {
      hub.sendChannelMessage(EARTHQUAKE_CHANNEL, truncate(formatQuake(f)));
    }
    lastReportHour = hour;
  }
}

const hub = new HubClient('quakealert');

hub.log('QuakeAlert starting...');
hub.connect();
setInterval(pollLoop, POLL_INTERVAL);
// Run first poll immediately
pollLoop();

process.on('SIGINT', () => { hub.close(); process.exit(0); });
process.on('SIGTERM', () => { hub.close(); process.exit(0); });
