import net from 'net';
const HUB='127.0.0.1', PORT=7777;
const ch=parseInt(process.argv[2]);
const msg=process.argv.slice(3).join(' ');
if(isNaN(ch)||!msg){console.error('Usage: node send-channel.js <channelIdx> <message>');process.exit(1);}
const sock=net.createConnection({host:HUB,port:PORT},()=>{
  sock.write(JSON.stringify({action:'register',name:'cli-sender'})+'\n');
  setTimeout(()=>{
    sock.write(JSON.stringify({action:'send_channel',channelIdx:ch,text:msg})+'\n');
    console.log('Sent ch='+ch+': '+msg);
    setTimeout(()=>{sock.end();process.exit(0);},2000);
  },1000);
});
sock.on('error',e=>{console.error('Error:',e.message);process.exit(1);});