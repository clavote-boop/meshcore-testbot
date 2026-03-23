// bot-quakealert.js — Continuous earthquake monitoring with 5-tier escalating alerts
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

// Constants
const POLL_INTERVAL = 60000;
const USGS_BASE = 'https://earthquake.usgs.gov/fdsnws/event/1/query';
const CALIFORNIA_LAT = 36.7783;
const CALIFORNIA_LON = -119.4179;
const CA_RADIUS_KM = 500;
const MAX_MSG_BYTES = 190;

// Channel assignments
const CH_PUBLIC = 0;
const CH_TEST = 1;
const CH_EARTHQUAKE_BAYAREA = 7;
const CH_EARTHQUAKE_LA = 8;
const CH_EARTHQUAKE_SD = 9;
const CH_EARTHQUAKE = 10;
const CH_GUZMAN = 4;

// Metro regions with proximity thresholds
const REGIONS = [
 { name: 'Bay Area', lat: 37.7749, lon: -122.4194, radiusKm: 150, channel: CH_EARTHQUAKE_BAYAREA },
 { name: 'Los Angeles', lat: 34.0522, lon: -118.2437, radiusKm: 150, channel: CH_EARTHQUAKE_LA },
 { name: 'San Diego', lat: 32.7157, lon: -117.1611, radiusKm: 100, channel: CH_EARTHQUAKE_SD },
];

const QUIPS = [
 'Stay grounded out there!', 'That really shook things up!',
 'Rock and roll, California style!', 'Mother Earth just stretched.',
 'Seismographs say hi!', 'The earth moved - and it wasnt love.',
 'Plate tectonics: never a dull moment.', 'Just another day on the Ring of Fire.',
 'The ground has opinions today.', 'Shake it off - literally.',
 'Geology in action!', 'The fault is not yours.',
 'Earth: still under construction.', 'Tectonic tango.',
 'Nature reminding us who is boss.'
];

const REPORTED_IDS = new Set();
let lastReportHour = null;

function truncate(text) {
 const buf = Buffer.from(text, 'utf8');
 if (buf.length <= MAX_MSG_BYTES) return text;
 return buf.slice(0, MAX_MSG_BYTES - 3).toString('utf8') + '...';
}

function httpGet(url) {
 return new Promise((resolve, reject) => {
 https.get(url, res => {
 let data = '';
 res.on('data', c => { data += c; });
 res.on('end', () => { try { resolve(JSON.parse(data)); } catch (e) { reject(e); } });
 }).on('error', reject);
 });
}

function formatQuake(f) {
 const mag = f.properties.mag;
 const place = f.properties.place;
 const t = new Date(f.properties.time);
 const hh = t.getUTCHours().toString().padStart(2, '0');
 const mm = t.getUTCMinutes().toString().padStart(2, '0');
 return `M${mag.toFixed(1)} ${place} ${hh}:${mm}UTC`;
}

function randomQuip() {
 return QUIPS[Math.floor(Math.random() * QUIPS.length)];
}

function haversineKm(lat1, lon1, lat2, lon2) {
 const toRad = d => d * Math.PI / 180;
 const R = 6371;
 const dLat = toRad(lat2 - lat1);
 const dLon = toRad(lon2 - lon1);
 const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
 return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function getRegionalChannels(lat, lon) {
 const channels = [];
 for (const r of REGIONS) {
 if (haversineKm(lat, lon, r.lat, r.lon) <= r.radiusKm) channels.push(r.channel);
 }
 return channels;
}

function closestMetroKm(lat, lon) {
 let min = Infinity;
 for (const r of REGIONS) {
 const d = haversineKm(lat, lon, r.lat, r.lon);
 if (d < min) min = d;
 }
 return min;
}

function getTier(mag, lat, lon) {
 const metroDist = closestMetroKm(lat, lon);
 if (mag >= 7.0) return 5;
 if (mag >= 5.5) return 4;
 if (mag >= 4.5) return 3;
 if (mag >= 4.0 && metroDist <= 50) return 3;
 if (mag >= 3.5) return 2;
 if (mag >= 3.0 && metroDist <= 30) return 2;
 return 1;
}

function getRegionName(lat, lon) {
 let closest = null;
 let minD = Infinity;
 for (const r of REGIONS) {
 const d = haversineKm(lat, lon, r.lat, r.lon);
 if (d < minD) { minD = d; closest = r.name; }
 }
 return closest || 'California';
}

async function alertQuake(feature) {
 const mag = feature.properties.mag;
 const lat = feature.geometry.coordinates[1];
 const lon = feature.geometry.coordinates[0];
 const tier = getTier(mag, lat, lon);
 const regional = getRegionalChannels(lat, lon);
 const region = getRegionName(lat, lon);
 const base = formatQuake(feature);
 hub.log(`Tier ${tier} alert: ${base}`);
 if (tier === 1) {
 const msg = `${base} | ${randomQuip()}`;
 hub.sendChannelMessage(CH_EARTHQUAKE, truncate(msg));
 } else if (tier === 2) {
 const msg = `Felt that? ${base}. No damage expected. ${randomQuip()}`;
 hub.sendChannelMessage(CH_EARTHQUAKE, truncate(msg));
 for (const ch of regional) hub.sendChannelMessage(ch, truncate(msg));
 } else if (tier === 3) {
 const eqMsg = `${base}. Light shaking reported near ${region}. No tsunami risk.`;
 const pubMsg = `M${mag.toFixed(1)} earthquake near ${region}. Details on #earthquake.`;
 hub.sendChannelMessage(CH_EARTHQUAKE, truncate(eqMsg));
 for (const ch of regional) hub.sendChannelMessage(ch, truncate(eqMsg));
 hub.sendChannelMessage(CH_PUBLIC, truncate(pubMsg));
 } else if (tier === 4) {
 const eqMsg = `${base}. Moderate-to-strong shaking near ${region}. Check for damage, secure gas if you smell it.`;
 const pubMsg = `M${mag.toFixed(1)} earthquake near ${region}. Check for damage. Updates on #earthquake.`;
 hub.sendChannelMessage(CH_EARTHQUAKE, truncate(eqMsg));
 for (const ch of regional) hub.sendChannelMessage(ch, truncate(eqMsg));
 hub.sendChannelMessage(CH_PUBLIC, truncate(pubMsg));
 } else if (tier === 5) {
 const eqMsg = `${base}. STRONG SHAKING. Drop Cover Hold. Check gas. Move away from damaged structures.`;
 const pubMsg = `M${mag.toFixed(1)} earthquake near ${region}. Drop Cover Hold. Stay off roads unless evacuating. Updates on #earthquake.`;
 hub.sendChannelMessage(CH_EARTHQUAKE, truncate(eqMsg));
 for (const ch of regional) hub.sendChannelMessage(ch, truncate(eqMsg));
 hub.sendChannelMessage(CH_PUBLIC, truncate(pubMsg));
 setTimeout(() => {
 hub.sendChannelMessage(CH_PUBLIC, truncate(`REPEAT: ${pubMsg}`));
 }, 30000);
 }
}

async function fetchCaliforniaQuakes() {
 const now = new Date();
 const end = now.toISOString();
 const start = new Date(now.getTime() - 60 * 60 * 1000).toISOString();
 const url = `${USGS_BASE}?format=geojson&latitude=${CALIFORNIA_LAT}&longitude=${CALIFORNIA_LON}&maxradiuskm=${CA_RADIUS_KM}&starttime=${start}&endtime=${end}&limit=20&minmagnitude=2.0`;
 return await httpGet(url);
}

async function pollLoop() {
 try {
 const data = await fetchCaliforniaQuakes();
 const features = data.features || [];
 for (const f of features) {
 if (REPORTED_IDS.has(f.id)) continue;
 await alertQuake(f);
 REPORTED_IDS.add(f.id);
 }
 if (REPORTED_IDS.size > 1000) {
 const ids = Array.from(REPORTED_IDS);
 for (let i = 0; i < Math.floor(ids.length / 2); i++) REPORTED_IDS.delete(ids[i]);
 }
 await scheduledReport();
 } catch (e) { hub.log(`Poll error: ${e.message}`); }
}

async function scheduledReport() {
 const now = new Date();
 const hour = now.getUTCHours();
 if ((hour === 13 || hour === 1) && lastReportHour !== hour) {
 const end = now.toISOString();
 const start = new Date(now.getTime() - 12 * 3600 * 1000).toISOString();
 const url = `${USGS_BASE}?format=geojson&latitude=${CALIFORNIA_LAT}&longitude=${CALIFORNIA_LON}&maxradiuskm=${CA_RADIUS_KM}&starttime=${start}&endtime=${end}&limit=50&minmagnitude=1.0&orderby=magnitude`;
 const data = await httpGet(url);
 const features = data.features || [];
 const period = hour === 13 ? 'AM' : 'PM';
 hub.sendChannelMessage(CH_EARTHQUAKE, `CA Quake Report ${period} - ${features.length} events (12h)`);
 for (const f of features.slice(0, 10)) {
 hub.sendChannelMessage(CH_EARTHQUAKE, truncate(formatQuake(f)));
 }
 if (features.length === 0) hub.sendChannelMessage(CH_EARTHQUAKE, 'All quiet. No events recorded.');
 lastReportHour = hour;
 }
}

const hub = new HubClient('quakealert');
hub.log('QuakeAlert starting...');
hub.connect();
setInterval(pollLoop, POLL_INTERVAL);
pollLoop();

process.on('SIGINT', () => { hub.close(); process.exit(0); });
// Simulation command
hub.on('channel_message', async (msg) => {
  if (msg.channelIdx !== CH_GUZMAN) return;
  if (msg.senderName === 'Clem Heavyside') return;
  const text = (msg.text || '').trim();
  const m = text.match(/^!simquake\s+(\d)$/i);
  if (!m) return;
  const tier = parseInt(m[1], 10);
  const mags = {1:2.5,2:3.5,3:4.5,4:5.8,5:7.2};
  const mag = mags[tier];
  if (!mag) return;
  const fakeFeature = {
    properties: { mag, place: 'Simulated near San Jose', time: Date.now(), alert: null },
    geometry: { coordinates: [-121.84, 37.27] }
  };
  const base = formatQuake(fakeFeature);
  const regional = getRegionalChannels(37.27, -121.84);
  const region = getRegionName(37.27, -121.84);
  hub.sendChannelMessage(CH_GUZMAN, truncate(`[SIM] Tier ${tier} alert: ${base}`));
  hub.sendChannelMessage(CH_GUZMAN, truncate(`[SIM] Would route to channels: ${regional.join(', ') || 'none'} in region ${region}`));
  hub.sendChannelMessage(CH_GUZMAN, truncate('[SIM] End. No real alerts sent.'));
});

process.on('SIGTERM', () => { hub.close(); process.exit(0); });
