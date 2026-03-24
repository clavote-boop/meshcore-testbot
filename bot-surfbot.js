// bot-surfbot.js — Surfbot plugin for mesh-hub
// Provides surf spot info with marine, wind, and tide data
import HubClient from './hub-client.js';
import https from 'https';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import dotenv from 'dotenv';

// Load global env
dotenv.config();
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const envPath = path.join(__dirname, '.env');
if (fs.existsSync(envPath)) {
  dotenv.config({ path: envPath });
}

const MY_NODE_NAME = process.env.MY_NODE_NAME || 'surfbot';
const DEFAULT_LAT = parseFloat(process.env.DEFAULT_LAT || '0');
const DEFAULT_LON = parseFloat(process.env.DEFAULT_LON || '0');
const MAX_MSG_BYTES = 190;

const SURF_SPOTS = [
  { name: 'Steamer Lane', lat: 36.951, lon: -122.026, noaaStation: 9413450 },
  { name: 'Pleasure Point', lat: 36.964, lon: -121.976, noaaStation: 9413450 },
  { name: 'Pacifica/Linda Mar', lat: 37.592, lon: -122.500, noaaStation: 9414290 },
  { name: 'Ocean Beach SF', lat: 37.760, lon: -122.514, noaaStation: 9414290 },
  { name: 'Mavericks', lat: 37.494, lon: -122.500, noaaStation: 9414290 },
  { name: 'Santa Cruz Harbor', lat: 36.963, lon: -122.001, noaaStation: 9413450 },
  { name: 'Capitola', lat: 36.972, lon: -121.953, noaaStation: 9413450 },
  { name: 'Manresa', lat: 36.935, lon: -121.866, noaaStation: 9413450 },
  { name: 'Monterey', lat: 36.613, lon: -121.893, noaaStation: 9413450 },
  { name: 'Moss Landing', lat: 36.804, lon: -121.789, noaaStation: 9413450 },
  { name: 'Bolinas', lat: 37.908, lon: -122.731, noaaStation: 9414958 },
  { name: 'Stinson Beach', lat: 37.900, lon: -122.643, noaaStation: 9414958 },
  { name: 'Half Moon Bay', lat: 37.503, lon: -122.473, noaaStation: 9414290 },
  { name: 'Ano Nuevo', lat: 37.108, lon: -122.338, noaaStation: 9414290 },
  { name: 'Davenport', lat: 37.012, lon: -122.190, noaaStation: 9413450 },
  { name: 'Huntington Beach', lat: 33.655, lon: -118.005, noaaStation: 9410660 },
  { name: 'Trestles', lat: 33.382, lon: -117.589, noaaStation: 9410230 },
  { name: 'Rincon', lat: 34.374, lon: -119.476, noaaStation: 9411340 },
  { name: 'Ventura', lat: 34.267, lon: -119.280, noaaStation: 9411340 },
  { name: 'Malibu', lat: 34.036, lon: -118.678, noaaStation: 9410660 }
];

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

function haversineMi(lat1, lon1, lat2, lon2) {
  const R = 3958.8;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function degToCompass(deg) {
  const dirs = ['N','NE','E','SE','S','SW','W','NW'];
  return dirs[Math.round(((deg % 360) / 45)) % 8];
}

function findClosestSpot(lat, lon) {
  let best = null;
  for (const spot of SURF_SPOTS) {
    const dist = haversineMi(lat, lon, spot.lat, spot.lon);
    if (dist <= 50 && (!best || dist < best.dist)) {
      best = { spot, dist };
    }
  }
  return best;
}

function metersToFeet(m) {
  return Math.round((m * 3.28084) * 10) / 10; // 1 decimal
}

async function fetchMarine(lat, lon) {
  const url = `https://marine-api.open-meteo.com/v1/marine?latitude=${lat}&longitude=${lon}&current=wave_height,wave_direction,wave_period,swell_wave_height,swell_wave_direction,swell_wave_period,wind_wave_height,wind_wave_direction,wind_wave_period&timezone=America/Los_Angeles`;
  return await httpGet(url);
}

async function fetchWind(lat, lon) {
  const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=wind_speed_10m,wind_direction_10m,wind_gusts_10m&wind_speed_unit=mph&timezone=America/Los_Angeles`;
  return await httpGet(url);
}

async function fetchTides(stationId) {
  const today = new Date().toISOString().split('T')[0];
  const url = `https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?date=${today}&product=predictions&datum=MLLW&time_zone=lst_ldt&interval=hilo&units=english&format=json&station=${stationId}`;
  return await httpGet(url);
}

function truncate(text) {
  const buf = Buffer.from(text, 'utf8');
  if (buf.length <= MAX_MSG_BYTES) return text;
  return buf.slice(0, MAX_MSG_BYTES - 3).toString('utf8') + '...';
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

const hubClient = new HubClient({ nodeName: MY_NODE_NAME });

hubClient.on('channel_message', async (msg) => {
  const text = (msg.text || '').trim();
  const colonIdx = text.indexOf(': ');
  const requester = colonIdx > 0 ? text.substring(0, colonIdx) : (msg.senderName || 'anon');
  const afterColon = colonIdx > 0 ? text.substring(colonIdx + 2) : text;

  if (!/surfbot/i.test(afterColon)) return;

  const locMatch = afterColon.match(/surfbot\s+(.+)/i);
  let lat = DEFAULT_LAT, lon = DEFAULT_LON, label = 'Default Location';
  if (locMatch) {
    const geo = await geocodeLocation(locMatch[1].trim());
    if (geo) { lat = geo.lat; lon = geo.lon; label = geo.name; }
  }

  const closest = findClosestSpot(lat, lon);
  if (!closest) {
    hubClient.sendChannelMessage(msg.channelIdx, `@${requester}: No surf spots found within 50 miles.`);
    return;
  }
  const { spot, dist } = closest;

  try {
    const [marine, wind, tides] = await Promise.all([
      fetchMarine(spot.lat, spot.lon),
      fetchWind(spot.lat, spot.lon),
      fetchTides(spot.noaaStation)
    ]);

    // Marine data (swell)
    const swellHeightFt = metersToFeet(marine.current.swell_wave_height || 0);
    const swellDir = degToCompass(marine.current.swell_wave_direction || 0);
    const swellPeriod = marine.current.swell_wave_period || '';
    const msg1 = `@${requester}: ${spot.name} (${dist.toFixed(1)}mi) Swell ${swellHeightFt}ft@${swellPeriod}s ${swellDir}`;
    hubClient.sendChannelMessage(msg.channelIdx, truncate(msg1));

    // Wind & wave data
    const windSpeed = wind.current.wind_speed_10m || '';
    const windDir = degToCompass(wind.current.wind_direction_10m || 0);
    const gust = wind.current.wind_gusts_10m || '';
    const waveHeightFt = metersToFeet(marine.current.wave_height || 0);
    const waveDir = degToCompass(marine.current.wave_direction || 0);
    const wavePeriod = marine.current.wave_period || '';
    const msg2 = `Wind: ${windSpeed} mph ${windDir} gusts ${gust} | Waves: ${waveHeightFt}ft@${wavePeriod}s ${waveDir}`;
    hubClient.sendChannelMessage(msg.channelIdx, truncate(msg2));

    // Tides formatting
    const tideEvents = (tides.predictions || []).map(p => `${p.type.charAt(0)} ${p.height}ft ${p.time}`);
    const msg3 = `Tides: ${tideEvents.join(' ')}`;
    hubClient.sendChannelMessage(msg.channelIdx, truncate(msg3));
  } catch (e) {
    hubClient.sendChannelMessage(msg.channelIdx, `@${requester}: Surfbot error - ${e.message}`);
  }
});

hubClient.log && hubClient.log('Surfbot starting...');
hubClient.connect();

process.on('SIGINT', () => { hubClient.close && hubClient.close(); process.exit(0); });
process.on('SIGTERM', () => { hubClient.close && hubClient.close(); process.exit(0); });
