// bot-surfbot.js — Surfbot plugin for mesh-hub
// Provides surf spot info with marine, wind, and tide data (including pagination).
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

const MY_NODE_NAME = process.env.MY_NODE_NAME || 'surfbot';
const DEFAULT_LAT = parseFloat(process.env.DEFAULT_LAT || '0');
const DEFAULT_LON = parseFloat(process.env.DEFAULT_LON || '0');
const MAX_MSG_BYTES = 190;

const SURF_SPOTS = [
  { name: 'Steamer Lane', lat: 36.951, lon: -122.026, noaaStation: 9413450 },
  { name: 'Pleasure Point', lat: 36.964, lon: -121.976, noaaStation: 9413450 },
  { name: 'Pacifica/Linda Mar', lat: 37.592, lon: -122.5, noaaStation: 9414290 },
  { name: 'Ocean Beach SF', lat: 37.76, lon: -122.514, noaaStation: 9414290 },
  { name: 'Mavericks', lat: 37.494, lon: -122.5, noaaStation: 9414290 },
  { name: 'Santa Cruz Harbor', lat: 36.963, lon: -122.001, noaaStation: 9413450 },
  { name: 'Capitola', lat: 36.972, lon: -121.953, noaaStation: 9413450 },
  { name: 'Manresa', lat: 36.935, lon: -121.866, noaaStation: 9413450 },
  { name: 'Monterey', lat: 36.613, lon: -121.893, noaaStation: 9413450 },
  { name: 'Moss Landing', lat: 36.804, lon: -121.789, noaaStation: 9413450 },
  { name: 'Bolinas', lat: 37.908, lon: -122.731, noaaStation: 9414958 },
  { name: 'Stinson Beach', lat: 37.9, lon: -122.643, noaaStation: 9414958 },
  { name: 'Half Moon Bay', lat: 37.503, lon: -122.473, noaaStation: 9414290 },
  { name: 'Ano Nuevo', lat: 37.108, lon: -122.338, noaaStation: 9414290 },
  { name: 'Davenport', lat: 37.012, lon: -122.19, noaaStation: 9413450 },
  { name: 'Huntington Beach', lat: 33.655, lon: -118.005, noaaStation: 9410660 },
  { name: 'Trestles', lat: 33.382, lon: -117.589, noaaStation: 9410230 },
  { name: 'Rincon', lat: 34.374, lon: -119.476, noaaStation: 9411340 },
  { name: 'Ventura', lat: 34.267, lon: -119.28, noaaStation: 9411340 },
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
  // Use literal "today" and include high/low interval
  const url = `https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?date=today&product=predictions&datum=MLLW&time_zone=lst_ldt&units=english&format=json&station=${stationId}&interval=hilo`;
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

// Session map for pagination (expires after 5 minutes of inactivity)
const userSessions = new Map(); // requester -> {spots: [{spot,dist}], idx: number, lastTime: ms}

function sendEndOrMore(ch, requester, sess) {
 if (sess.idx < sess.spots.length) {
 hubClient.sendChannelMessage(ch, `Stoked! Reply Y for more spots`);
 } else {
 hubClient.sendChannelMessage(ch, `@${requester}: Thats all the breaks bro! Grab your board and go shred it!`);
 userSessions.delete(requester);
 }
}

hubClient.on('channel_message', async (msg) => {
  const text = (msg.text || '').trim();
  const colonIdx = text.indexOf(': ');
  const requester = colonIdx > 0 ? text.substring(0, colonIdx) : (msg.senderName || 'anon');
  const afterColon = colonIdx > 0 ? text.substring(colonIdx + 2) : text;

  // Pagination request – just "Y" (case‑insensitive)
  if (/^Y$/i.test(afterColon) && userSessions.has(requester)) {
    const sess = userSessions.get(requester);
    // Clean up if stale
    if (Date.now() - sess.lastTime > 5 * 60 * 1000) { userSessions.delete(requester); return; }
    const nextBatch = sess.spots.slice(sess.idx, sess.idx + 1);
    if (nextBatch.length === 0) {
      hubClient.sendChannelMessage(msg.channelIdx, `@${requester}: Thats all the breaks bro! Grab your board and go shred it!`);
      userSessions.delete(requester);
      return;
    }
    for (const { spot, dist } of nextBatch) {
      try {
        const [marine, wind, tides] = await Promise.all([
          fetchMarine(spot.lat, spot.lon),
          fetchWind(spot.lat, spot.lon),
          fetchTides(spot.noaaStation)
        ]);
        const swellHeightFt = metersToFeet(marine.current.swell_wave_height || 0);
        const swellDir = degToCompass(marine.current.swell_wave_direction || 0);
        const swellDeg = Math.round(marine.current.swell_wave_direction || 0);
        const windSpeed = wind.current.wind_speed_10m || '';
        const windDir = degToCompass(wind.current.wind_direction_10m || 0);
        // Use first tide prediction for compact view
        let tideStr = '';
        if (tides.predictions && tides.predictions.length > 0) {
          const p = tides.predictions[0];
          const time = p.t.split(' ')[1].replace(/^0/, '');
          tideStr = `${p.type} ${p.v}ft @${time}`;
        }
        const msgTxt = `@${requester}: ${spot.name} (${dist.toFixed(1)}mi) Swell ${swellHeightFt}ft ${swellDeg}deg${swellDir} Wind ${windSpeed}mph ${windDir} | Tide ${tideStr}`;
        hubClient.sendChannelMessage(msg.channelIdx, truncate(msgTxt));
      } catch (e) {
        hubClient.sendChannelMessage(msg.channelIdx, `@${requester}: Surfbot error - ${e.message}`);
      }
    }
    sess.idx += 2;
    sess.lastTime = Date.now();
    if (sess.idx >= sess.spots.length) {
      hubClient.sendChannelMessage(msg.channelIdx, `@${requester}: Thats all the breaks bro! Grab your board and go shred it!`);
      userSessions.delete(requester);
    } else {
      sendEndOrMore(msg.channelIdx, requester, sess);
    }
    return;
  }

  // Main surfbot request – must contain the word "surfbot"
  if (!/surfbot/i.test(afterColon)) return;

  const locMatch = afterColon.match(/surfbot\s+(.+)/i);
  let lat = DEFAULT_LAT, lon = DEFAULT_LON, label = 'Default Location';
  if (locMatch) {
    const geo = await geocodeLocation(locMatch[1].trim());
    if (geo) { lat = geo.lat; lon = geo.lon; label = geo.name; }
  }

  // Compute distances for all spots and sort
  const distances = SURF_SPOTS.map(spot => ({ spot, dist: haversineMi(lat, lon, spot.lat, spot.lon) }));
  distances.sort((a, b) => a.dist - b.dist).filter(d => d.dist <= 200).slice(0, 7);
  const primary = distances[0];

  // Store session for pagination (skip the first spot which is already shown)
  userSessions.set(requester, { spots: distances, idx: 1, lastTime: Date.now() });

  try {
    const [marine, wind, tides] = await Promise.all([
      fetchMarine(primary.spot.lat, primary.spot.lon),
      fetchWind(primary.spot.lat, primary.spot.lon),
      fetchTides(primary.spot.noaaStation)
    ]);

    // Surf message (swell + wind + waves combined)
    const swellHeightFt = metersToFeet(marine.current.swell_wave_height || 0);
    const swellDir = degToCompass(marine.current.swell_wave_direction || 0);
    const swellPeriod = marine.current.swell_wave_period || '';
    const windSpeed = wind.current.wind_speed_10m || '';
    const windDir = degToCompass(wind.current.wind_direction_10m || 0);
    const gust = wind.current.wind_gusts_10m || '';
    const waveHeightFt = metersToFeet(marine.current.wave_height || 0);
    const waveDir = degToCompass(marine.current.wave_direction || 0);
    const wavePeriod = marine.current.wave_period || '';
    const msg1 = `@${requester}: ${primary.spot.name} (${primary.dist.toFixed(1)}mi) Swl ${swellHeightFt}ft@${swellPeriod}s ${swellDeg}deg${swellDir} Wnd ${windSpeed}mph ${windDir} g${gust} Wav ${waveHeightFt}ft@${wavePeriod}s ${waveDir}`;
    hubClient.sendChannelMessage(msg.channelIdx, truncate(msg1));

    // Tide message – use NOAA format (v, t)
    function formatTideTime(t24) {
      const [hh, mm] = t24.split(':');
      let h = parseInt(hh, 10);
      const ampm = h >= 12 ? 'pm' : 'am';
      if (h > 12) h -= 12;
      if (h === 0) h = 12;
      return h + ':' + mm + ampm;
    }
    const tideEvents = (tides.predictions || []).map(p => {
      const t = formatTideTime(p.t.split(' ')[1]);
      const h = parseFloat(p.v).toFixed(1);
      return p.type + ' ' + h + 'ft @' + t;
    });
    const msg3 = `Tides: ${tideEvents.join(' ')}`;
    hubClient.sendChannelMessage(msg.channelIdx, truncate(msg3));

    sendEndOrMore(msg.channelIdx, requester, userSessions.get(requester));
  } catch (e) {
    hubClient.sendChannelMessage(msg.channelIdx, `@${requester}: Surfbot error - ${e.message}`);
  }
});

hubClient.log && hubClient.log('Surfbot starting...');
hubClient.connect();

process.on('SIGINT', () => { hubClient.close && hubClient.close(); process.exit(0); });
process.on('SIGTERM', () => { hubClient.close && hubClient.close(); process.exit(0); });
