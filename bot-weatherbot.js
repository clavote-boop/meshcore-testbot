// bot-weatherbot.js — Weatherbot plugin for mesh-hub
// Responds to weatherbot commands with weather data from open-meteo
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

const hub = new HubClient('weatherbot');

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
 try { resolve(JSON.parse(d)); } catch(e) { reject(e); }
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

async function fetchWeather(lat, lon, label) {
 const url = 'https://api.open-meteo.com/v1/forecast?latitude=' + lat + '&longitude=' + lon + '&current=temperature_2m,relative_humidity_2m,wind_speed_10m&daily=temperature_2m_max,temperature_2m_min&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=auto';
 const j = await httpGet(url);
 const c = j.current;
 let forecast = '';
 if (j.daily && j.daily.time) {
 for (let i = 0; i < j.daily.time.length; i++) {
 forecast += j.daily.time[i].slice(5) + ':' + j.daily.temperature_2m_max[i] + '/' + j.daily.temperature_2m_min[i] + 'F ';
 }
 }
 return {
 current: label + ' ' + c.temperature_2m + 'F H' + c.relative_humidity_2m + '% W' + c.wind_speed_10m + 'mph',
 forecast: forecast.trim()
 };
}

hub.on('channel_message', async (msg) => {
    const ALLOWED = [0, 1, 4];
    if (!ALLOWED.includes(msg.channelIdx)) return;
 if (msg.senderName === MY_NODE_NAME) return;

 const text = (msg.text || '').trim();
 const lower = text.toLowerCase();

 if (!lower.includes('weatherbot')) return;

 hub.log(`Weather request from ${msg.senderName} on ch=${msg.channelIdx}: ${text}`);

 let wlat = DEFAULT_LAT, wlon = DEFAULT_LON, wlabel = DEFAULT_LABEL;
 const colonIdx = text.indexOf(": ");
 const requesterName = colonIdx > 0 ? text.substring(0, colonIdx) : (msg.senderName || "anon");
 const locMatch = text.match(/weatherbot\s+(.+)/i);
 if (locMatch) {
 try {
 const geo = await geocodeLocation(locMatch[1].trim());
 if (geo) { wlat = geo.lat; wlon = geo.lon; wlabel = geo.name; }
 } catch(e) { hub.log(`Geocode error: ${e.message}`); }
 }

 try {
 const weather = await fetchWeather(wlat, wlon, wlabel);
 const reply = truncate("@" + requesterName + ": " + weather.current);
 hub.log(`Reply: ${reply}`);
 hub.sendChannelMessage(msg.channelIdx, reply);

 if (weather.forecast) {
 // Send forecast as follow-up after 1.5s delay
 setTimeout(() => {
 const fReply = truncate('Fcst:' + weather.forecast);
 hub.sendChannelMessage(msg.channelIdx, fReply);
 }, 2000);
 }
 } catch(e) {
 hub.log(`Weather error: ${e.message}`);
 hub.sendChannelMessage(msg.channelIdx, truncate('Weather unavailable: ' + e.message));
 }
});

hub.log('Weatherbot starting...');
hub.connect();

process.on('SIGINT', () => { hub.close(); process.exit(0); });
process.on('SIGTERM', () => { hub.close(); process.exit(0); });
