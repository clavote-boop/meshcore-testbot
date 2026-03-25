// bot-gasbot.js — Gasbot plugin for mesh-hub
// Finds closest gas stations near your location with navigation links (URL included)
import HubClient from './hub-client.js';
import https from 'https';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import dotenv from 'dotenv';

dotenv.config();
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const envPath = path.join(__dirname, '.env');
if (fs.existsSync(envPath)) {
  dotenv.config({ path: envPath });
}

const MY_NODE_NAME = process.env.MY_NODE_NAME || 'gasbot';
const DEFAULT_LAT = parseFloat(process.env.DEFAULT_LAT || '37.2713');
const DEFAULT_LON = parseFloat(process.env.DEFAULT_LON || '-121.8366');
const DEFAULT_LABEL = 'San Jose';
const MAX_MSG_BYTES = 190;
const GOOGLE_API_KEY = process.env.GOOGLE_API_KEY || '';
const PAGE_SIZE = 4;
const MAX_PAGES = 5; // max 5 pages (20 stations)
const SESSION_TTL = 10 * 60 * 1000; // 10 minutes

const hubClient = new HubClient({ nodeName: MY_NODE_NAME });
const userSessions = new Map();

function truncate(text) {
  const buf = Buffer.from(text, 'utf8');
  if (buf.length <= MAX_MSG_BYTES) return text;
  return buf.slice(0, MAX_MSG_BYTES - 3).toString('utf8') + '...';
}

function httpGet(url) {
  return new Promise((resolve, reject) => {
    https.get(url, res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        try { resolve(JSON.parse(d)); } catch (e) { reject(e); }
      });
    }).on('error', reject);
  });
}

function httpPost(url, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const options = {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(data),
        'X-Goog-Api-Key': GOOGLE_API_KEY,
        'X-Goog-FieldMask': 'places.displayName,places.location,places.fuelOptions,places.id'
      }
    };
    const req = https.request(url, options, res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        try { resolve(JSON.parse(d)); } catch (e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

async function geocodeLocation(query) {
  const url = 'https://geocoding-api.open-meteo.com/v1/search?name=' +
    encodeURIComponent(query) + '&count=1&language=en&format=json';
  const j = await httpGet(url);
  if (j.results && j.results.length > 0) {
    return { lat: j.results[0].latitude, lon: j.results[0].longitude, name: j.results[0].name };
  }
  return null;
}

function haversineMi(lat1, lon1, lat2, lon2) {
  const R = 3958.8;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
    Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function bearing(lat1, lon1, lat2, lon2) {
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const y = Math.sin(dLon) * Math.cos(lat2 * Math.PI / 180);
  const x = Math.cos(lat1 * Math.PI / 180) * Math.sin(lat2 * Math.PI / 180) -
    Math.sin(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.cos(dLon);
  let deg = Math.atan2(y, x) * 180 / Math.PI;
  deg = (deg + 360) % 360;
  const dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  return dirs[Math.round(deg / 45) % 8];
}

async function fetchAllGasStations(lat, lon, label) {
  const url = 'https://places.googleapis.com/v1/places:searchNearby';
  const body = {
    includedTypes: ['gas_station'],
    maxResultCount: 20,
    rankPreference: 'DISTANCE',
    locationRestriction: {
      circle: {
        center: { latitude: lat, longitude: lon },
        radius: 16093
      }
    }
  };
  const resp = await httpPost(url, body);
  if (!resp.places) return [];
  const stations = resp.places.map(p => {
    const name = p.displayName?.text || 'Unknown';
    const lat2 = p.location?.latitude;
    const lon2 = p.location?.longitude;
    const dist = haversineMi(lat, lon, lat2, lon2).toFixed(1);
    // Extract REGULAR_UNLEADED price if available
    let price = '';
    if (p.fuelOptions && p.fuelOptions.fuelPrices) {
      const fp = p.fuelOptions.fuelPrices.find(fp => fp.type === 'REGULAR_UNLEADED');
      if (fp && fp.price) {
        const d = Number(fp.price.units || 0) + (fp.price.nanos || 0) / 1e9;
        price = '$' + d.toFixed(3);
      }
    }
    // Build Google Maps URL (spaces -> +)
    const mapUrl = 'maps.google.com/maps?q=' + encodeURIComponent(name + ' ' + label).replace(/%20/g, '+');
    return { name, price, dist: parseFloat(dist), mapUrl };
  });
  stations.sort((a, b) => a.dist - b.dist);
  // Limit to max pages * page size (20 stations)
  return stations.slice(0, PAGE_SIZE * MAX_PAGES);
}

function cleanupSessions() {
  const now = Date.now();
  for (const [k, sess] of userSessions.entries()) {
    if (now - sess.lastTime > SESSION_TTL) userSessions.delete(k);
  }
}

hubClient.on('channel_message', async (msg) => {
  const ALLOWED = [1, 4];
  if (!ALLOWED.includes(msg.channelIdx)) return;
  cleanupSessions();
  if (msg.senderName === MY_NODE_NAME) return;
  const text = (msg.text || '').trim();
  const colonIdx = text.indexOf(': ');
  const requester = colonIdx > 0 ? text.substring(0, colonIdx) : (msg.senderName || 'anon');
  const afterColon = colonIdx > 0 ? text.substring(colonIdx + 2) : text;

  // Pagination request – user types "y"
  if (afterColon.trim().toLowerCase() === 'y' && userSessions.has(requester)) {
    const sess = userSessions.get(requester);
    const start = sess.page * PAGE_SIZE;
    const slice = sess.stations.slice(start, start + PAGE_SIZE);
    if (slice.length === 0) {
      hubClient.sendChannelMessage(msg.channelIdx, `@${requester}: No more stations. Try a new location.`);
      if (sess.total > PAGE_SIZE * MAX_PAGES) {
        hubClient.sendChannelMessage(msg.channelIdx, `@${requester}: Join #gasbot for extended search`);
      }
    } else {
      const startNum = start + 1;
      const endNum = start + slice.length;
      hubClient.sendChannelMessage(msg.channelIdx, `@${requester}: Gasbot ${sess.label} - showing ${startNum}-${endNum} of ${sess.total}`);
      for (const s of slice) {
        const line = `${s.name} ${s.price} ${s.dist}mi ${s.mapUrl}`;
        hubClient.sendChannelMessage(msg.channelIdx, truncate(line));
      }
      hubClient.sendChannelMessage(msg.channelIdx, `Reply 'y' for next stations`);
      sess.page += 1;
      sess.lastTime = Date.now();
    }
    return;
  }

  // Ignore messages that don't mention gasbot keyword
  if (!text.toLowerCase().includes('gasbot')) return;

  // Location command – "gasbot [city]" or just "gasbot"
  const locMatch = afterColon.match(/gasbot\s+(.+)/i);
  let wlat = DEFAULT_LAT, wlon = DEFAULT_LON, wlabel = DEFAULT_LABEL;
  if (locMatch) {
    try {
      const geo = await geocodeLocation(locMatch[1].trim());
      if (geo) { wlat = geo.lat; wlon = geo.lon; wlabel = geo.name; }
    } catch (e) { hubClient.log && hubClient.log(`Geocode error: ${e.message}`); }
  }

  const distMi = ((msg.pathLen || 0) * 0.621371).toFixed(1);

  if (!GOOGLE_API_KEY) {
    hubClient.sendChannelMessage(msg.channelIdx, `@${requester}: Gasbot - API key not configured`);
    return;
  }

  try {
    const stations = await fetchAllGasStations(wlat, wlon, wlabel);
    if (stations.length === 0) {
      hubClient.sendChannelMessage(msg.channelIdx, `@${requester}: Gasbot ${wlabel} - No gas stations found (${distMi}mi)`);
      return;
    }
    const sess = { lat: wlat, lon: wlon, label: wlabel, stations, page: 1, total: stations.length, lastTime: Date.now() };
    userSessions.set(requester, sess);
    const shown = Math.min(PAGE_SIZE, stations.length);
    hubClient.sendChannelMessage(msg.channelIdx, `@${requester}: Gasbot ${wlabel} (${distMi}mi) - showing 1-${shown} of ${stations.length}`);
    const firstSlice = stations.slice(0, PAGE_SIZE);
    for (const s of firstSlice) {
      const line = `${s.name} ${s.price} ${s.dist}mi ${s.mapUrl}`;
      hubClient.sendChannelMessage(msg.channelIdx, truncate(line));
    }
    hubClient.sendChannelMessage(msg.channelIdx, `Reply 'y' for next stations`);
  } catch (e) {
    hubClient.log && hubClient.log(`Gas error: ${e.message}`);
    hubClient.sendChannelMessage(msg.channelIdx, `@${requester}: Gasbot error - ${e.message}`);
  }
});

hubClient.log && hubClient.log('Gasbot starting...');
hubClient.connect();

process.on('SIGINT', () => { hubClient.close && hubClient.close(); process.exit(0); });
process.on('SIGTERM', () => { hubClient.close && hubClient.close(); process.exit(0); });
