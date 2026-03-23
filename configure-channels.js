// configure-channels.js — Set up earthquake channels on mesh radio
import { NodeJSSerialConnection } from '@liamcottle/meshcore.js';
import crypto from 'crypto';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const CHANNELS = [
  { idx: 7, name: 'earthquake-bayarea' },
  { idx: 8, name: 'earthquake-la' },
  { idx: 9, name: 'earthquake-sd' },
  { idx: 10, name: 'earthquake' }
];

async function main() {
  const conn = new NodeJSSerialConnection("/dev/ttyUSB0");
  await conn.connect();
  console.log('Connected to radio');
  await sleep(2000);

  // Set channels with random keys
  for (const ch of CHANNELS) {
    const secret = crypto.randomBytes(16);
    console.log(`Setting channel ${ch.idx}: ${ch.name} key=${secret.toString('hex')}`);
    await conn.setChannel(ch.idx, ch.name, secret);
    await sleep(500);
  }

  console.log('\nVerifying channels...');
  for (const ch of CHANNELS) {
    const info = await conn.getChannel(ch.idx);
    console.log(`Channel ${ch.idx}: ${JSON.stringify(info)}`);
    await sleep(300);
  }

  console.log('\nDone! Disconnecting...');
  await conn.disconnect();
  process.exit(0);
}

main().catch((e) => {
  console.error('Error:', e);
  process.exit(1);
});
