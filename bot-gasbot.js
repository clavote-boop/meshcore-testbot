// bot-gasbot.js — Gasbot plugin for mesh-hub
// Finds closest/cheapest gas stations near your location
// Future: food, grocery, pharmacy
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
const DEFAULT_LAT = parseFloat(process.env.DEFAULT_LAT || '37.2713');
const DEFAULT_LON = parseFloat(process.env.DEFAULT_LON || '-121.8366');
const DEFAULT_LABEL = 'San Jose';
const MAX_MSG_BYTES = 190;
const GOOGLE_API_KEY = process.env.GOOGLE_API_KEY || '';

// Category constants for future expansion
const CATEGORIES = {
  gas: { keyword: 'gas_station', label: 'Gas' },
  food: { keyword: 'restaurant', label: 'Food' },
  grocery: { keyword: 'supermarket', label: 'Grocery' },
  pharmacy: { keyword: 'pharmacy', label: 'Pharmacy' },
};

const hub = new HubClient('gasbot');

function truncate(text) {
  const buf = Buffer.from(text, 'utf8');
  if (buf.length <= MAX_MSG_BYTES) return text;
  return buf.slice(0, MAX_MSG_BYTES - 3).toString('utf8') + '...';
}

function httpGet(url) {
  return new Promise((resolve, reject) => {
    https.get(url, res => {
      let d = '';
      res.on('data', c => { d += c; });
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { reject(e); } });
    }).on('error', reject);
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

// Haversine distance in miles
function haversineMi(lat1, lon1, lat2, lon2) {
  const R = 3958.8;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
    Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// Cardinal direction from point A to point B
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

// Google Places Nearby Search for gas stations
async function findNearbyGas(lat, lon, radiusMeters = 8047) {
  // 8047 meters ~ 5 miles
  const url = `https://maps.googleapis.com/maps/api/place/nearbysearch/json` +
    `?location=${lat},${lon}&radius=${radiusMeters}&type=gas_station` +
    `&rankby=prominence&key=${GOOGLE_API_KEY}`;
  return await httpGet(url);
}

// Google Places Details for fuel prices (Places API New)
async function getPlaceDetails(placeId) {
  const url = `https://maps.googleapis.com/maps/api/place/details/json` +
    `?place_id=${placeId}&fields=name,geometry,formatted_address,business_status` +
    `&key=${GOOGLE_API_KEY}`;
  return await httpGet(url);
}

// Format a single station result
function formatStation(station, fromLat, fromLon) {
  const name = station.name || 'Unknown';
  const sLat = station.geometry.location.lat;
  const sLng = station.geometry.location.lng;
  const dist = haversineMi(fromLat, fromLon, sLat, sLng).toFixed(1);
  const dir = bearing(fromLat, fromLon, sLat, sLng);
  const mapLink = `maps.google.com/?q=${sLat},${sLng}`;
  // Price info not reliably available from basic Nearby Search
  // Format: StationName 1.2mi NE maps.google.com/?q=lat,lon
  return { text: `${name} ${dist}mi ${dir}`, link: mapLink, dist: parseFloat(dist) };
}

hub.on('channel_message', async (msg) => {
  if (msg.senderName === MY_NODE_NAME) return;

  const text = (msg.text || '').trim();
  const lower = text.toLowerCase();
  if (!lower.includes('gasbot')) return;

  const colonIdx = text.indexOf(': ');
  const requesterName = colonIdx > 0 ? text.substring(0, colonIdx) : (msg.senderName || 'anon');
  let afterColon = colonIdx > 0 ? text.substring(colonIdx + 2) : text;

  const locMatch = afterColon.match(/gasbot\s+(.+)/i);
  let wlat = DEFAULT_LAT, wlon = DEFAULT_LON, wlabel = DEFAULT_LABEL;

  if (locMatch) {
    try {
      const geo = await geocodeLocation(locMatch[1].trim());
      if (geo) { wlat = geo.lat; wlon = geo.lon; wlabel = geo.name; }
    } catch (e) { hub.log(`Geocode error: ${e.message}`); }
  }

  const distMi = ((msg.pathLen || 0) * 0.621371).toFixed(1);

  try {
    if (!GOOGLE_API_KEY) {
      hub.sendChannelMessage(msg.channelIdx, truncate(`@${requesterName}: Gasbot - API key not configured`));
      return;
    }

    const data = await findNearbyGas(wlat, wlon);

    if (data.status !== 'OK' || !data.results || data.results.length === 0) {
      hub.sendChannelMessage(msg.channelIdx,
        truncate(`@${requesterName}: Gasbot ${wlabel} - No gas stations found nearby (${distMi}mi)`));
      return;
    }

    // Filter to open stations, sort by distance
    const stations = data.results
      .filter(s => s.business_status === 'OPERATIONAL' || !s.business_status)
      .map(s => formatStation(s, wlat, wlon))
      .sort((a, b) => a.dist - b.dist)
      .slice(0, 5);

    // Header
    hub.sendChannelMessage(msg.channelIdx,
      truncate(`@${requesterName}: Gasbot ${wlabel} (${distMi}mi)`));

    // Each station on its own message for clickable links
    for (const s of stations) {
      hub.sendChannelMessage(msg.channelIdx, truncate(`${s.text} ${s.link}`));
    }

  } catch (e) {
    hub.log(`Gas error: ${e.message}`);
    hub.sendChannelMessage(msg.channelIdx,
      truncate(`@${requesterName}: Gasbot error - ${e.message}`));
  }
});

hub.log('Gasbot starting...');
hub.connect();

process.on('SIGINT', () => { hub.close(); process.exit(0); });
process.on('SIGTERM', () => { hub.close(); process.exit(0); });
