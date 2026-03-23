// send-announce.js – send 4 announcement messages to GUZMAN channel
import net from 'net';

const channelIdx = 1;
const messages = [
  "Hi #test channel. Two new bots available from Clem Heavyside.",
  "Quotebot - send Quotebot for a random quote. Response includes your path distance to the bot in miles, handy for checking mesh reach.",
  "Weatherbot - send Weatherbot for local weather at your node location. For other areas send Weatherbot followed by a zip code, city, county, or state.",
  "Give them a try and have fun. 73 de Clem"
];

const client = net.createConnection({ host: '127.0.0.1', port: 7777 }, () => {
  // Register as announcer
  client.write(JSON.stringify({action:'register', name:'announcer'}) + '\n');
});

client.on('error', err => {
  console.error('Connection error:', err);
  process.exit(1);
});

let step = 0;

// after registration delay 1s
setTimeout(() => {
  sendNext();
}, 1000);

function sendNext() {
  if (step >= messages.length) {
    // all sent, wait 2s then exit
    setTimeout(() => process.exit(0), 2000);
    return;
  }
  const msg = messages[step];
  const payload = {action:'send_channel', channelIdx, text:msg};
  client.write(JSON.stringify(payload) + '\n');
  step++;
  // schedule next after 3s
  setTimeout(sendNext, 3000);
}
