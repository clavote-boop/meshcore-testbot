// hub-client.js — Shared client library for connecting bots to mesh-hub
import net from 'net';
import { EventEmitter } from 'events';

class HubClient extends EventEmitter {
 constructor(botName, hubHost = '127.0.0.1', hubPort = 7777) {
 super();
 this.botName = botName;
 this.hubHost = hubHost;
 this.hubPort = hubPort;
 this.socket = null;
 this.buffer = '';
 this.connected = false;
 this.hubConnected = false;
 this.channels = [];
 this.reconnectTimer = null;
 }

 connect() {
 if (this.socket) return;
 this.socket = net.createConnection({ host: this.hubHost, port: this.hubPort }, () => {
 this.connected = true;
 this.log('Connected to hub');
 this.send({ action: 'register', name: this.botName });
 this.emit('connected');
 });

 this.socket.on('data', (data) => {
 this.buffer += data.toString();
 let idx;
 while ((idx = this.buffer.indexOf('\n')) !== -1) {
 const line = this.buffer.slice(0, idx).trim();
 this.buffer = this.buffer.slice(idx + 1);
 if (line) this._handleMessage(line);
 }
 });

 this.socket.on('close', () => {
 this.connected = false;
 this.hubConnected = false;
 this.socket = null;
 this.log('Disconnected from hub, reconnecting in 3s...');
 this.emit('disconnected');
 this.reconnectTimer = setTimeout(() => this.connect(), 3000);
 });

 this.socket.on('error', (e) => {
 this.log(`Hub connection error: ${e.message}`);
 if (this.socket) { try { this.socket.destroy(); } catch(x) {} }
 this.socket = null;
 this.connected = false;
 this.reconnectTimer = setTimeout(() => this.connect(), 3000);
 });
 }

 _handleMessage(line) {
 try {
 const msg = JSON.parse(line);
 switch(msg.type) {
 case 'hub_state':
 this.hubConnected = msg.connected;
 this.channels = msg.channels || [];
 this.emit('hub_state', msg);
 break;
 case 'hub_connected':
 this.hubConnected = true;
 this.emit('hub_connected');
 break;
 case 'hub_disconnected':
 this.hubConnected = false;
 this.emit('hub_disconnected');
 break;
 case 'channel_message':
 this.emit('channel_message', msg);
 break;
 case 'contact_message':
 this.emit('contact_message', msg);
 break;
 case 'channels_update':
 this.channels = msg.channels || [];
 this.emit('channels_update', msg.channels);
 break;
 case 'contacts_update':
 this.emit('contacts_update', msg.contacts);
 break;
 default:
 this.emit('message', msg);
 }
 } catch(e) {
 this.log(`Bad message from hub: ${e.message}`);
 }
 }

 send(obj) {
 if (this.socket && this.connected) {
 this.socket.write(JSON.stringify(obj) + '\n');
 }
 }

 sendChannelMessage(channelIdx, text) {
 this.send({ action: 'send_channel', channelIdx, text });
 }

 findChannelByName(name) {
 return this.channels.find(c => c.name === name);
 }

 log(msg) {
 const ts = new Date().toISOString();
 console.log(`[${this.botName} ${ts}] ${msg}`);
 }

 close() {
 if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
 if (this.socket) this.socket.destroy();
 this.socket = null;
 this.connected = false;
 }
}

export default HubClient;
