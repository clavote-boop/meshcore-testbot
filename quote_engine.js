// Quote Engine v1.0 - Two-tier quote system with per-user tracking
// Tier 1: Hardcoded curated quotes (always available)
// Tier 2: ZenQuotes API background fetcher (continuous backfill)
import { readFile } from 'fs/promises';
import { join } from 'path';
import { fileURLToPath } from 'url';


const CURATED_QUOTES = [
  {q:'The obstacle is the way.',a:'Marcus Aurelius'},
  {q:'Know yourself.',a:'Socrates'},
  {q:'He who conquers himself is the mightiest warrior.',a:'Confucius'},
  {q:'A journey of a thousand miles begins with a single step.',a:'Lao Tzu'},
  {q:'Be kind, for everyone you meet is fighting a hard battle.',a:'Plato'},
  {q:'What we think, we become.',a:'Buddha'},
  {q:'Do not seek, seek not to be.',a:'Rumi'},
  {q:'Science is the poetry of reality.',a:'Richard Feynman'},
  {q:'Somewhere, something incredible is waiting to be known.',a:'Carl Sagan'},
  {q:'Nothing in life is to be feared, it is only to be understood.',a:'Marie Curie'},
  {q:'The greatest obstacle to discovery is not ignorance—it is the illusion of knowledge.',a:'Daniel J. Boorstin'},
  {q:'Imagination is more important than knowledge.',a:'Albert Einstein'},
  {q:'Life would be tragic if it were not comedic.',a:'Stephen Hawking'},
  {q:'I don\'t have enough time to be embarrassed at once.',a:'Nikola Tesla'},
  {q:'If I have seen further, it is by standing on the shoulders of giants.',a:'Isaac Newton'},
  {q:'The only thing we have to fear is fear itself.',a:'Franklin D. Roosevelt'},
  {q:'The most difficult thing is the decision to act, the rest is merely tenacity.',a:'Amelia Earhart'},
  {q:'Leadership is the capacity to translate vision into reality.',a:'Warren Bennis'},
  {q:'The greatest glory in living lies not in never falling, but in rising every time we fall.',a:'Nelson Mandela'},
  {q:'In the end, we will remember not the words of our enemies, but the silence of our friends.',a:'Martin Luther King Jr.'},
  {q:'Success is not final, failure is not fatal: it is the courage to continue that counts.',a:'Winston Churchill'},
  {q:'I have not failed. I\'ve just found 10,000 ways that won\'t work.',a:'Thomas Edison'},
  {q:'The real voyage of discovery consists not in seeking new landscapes, but in having new eyes.',a:'Marcel Proust'},
  {q:'Victory belongs to the most persevering.',a:'Napoleon Bonaparte'},
  {q:'A smooth sea never made a skilled sailor.',a:'Franklin D. Roosevelt'},
  {q:'He who is brave is free.',a:'Seneca'},
  {q:'It does not matter how slowly you go as long as you do not stop.',a:'Confucius'},
  {q:'The only real wisdom is knowing you know nothing.',a:'Socrates'},
  {q:'The man who moves a mountain begins by carrying away small stones.',a:'Confucius'},
  {q:'Art enables us to find ourselves and lose ourselves at the same time.',a:'Thomas Merton'},
  {q:'Every child is an artist. The problem is staying an artist when you grow up.',a:'Pablo Picasso'},
  {q:'I am not what happened to me. I am what I choose to become.',a:'Carl Jung'},
  {q:'The greatest wealth is to live content with little.',a:'Plato'},
  {q:'A picture is a poem without words.',a:'Horace'},
  {q:'All we have to decide is what to do with the time that is given us.',a:'J.R.R. Tolkien'},
  {q:'All that is gold does not glitter.',a:'J.R.R. Tolkien'},
  {q:'If you want to lift yourself up, lift up someone else.',a:'Booker T. Washington'},
  {q:'You miss 100% of the shots you don\'t take.',a:'Wayne Gretzky'},
  {q:'The true sign of intelligence is not knowledge but imagination.',a:'Albert Einstein'},
  {q:'The purpose of life is a life of purpose.',a:'Robert Byrne'},
  {q:'The only real prison is fear, and the only real freedom is love.',a:'Thich Nhat Hanh'},
  {q:'Do not wait to strike till the iron is hot; make it hot by striking.',a:'William Butler Yeats'},
  {q:'All the world is a stage, and we are merely actors.',a:'William Shakespeare'},
  {q:'The only thing necessary for the triumph of evil is for good men to do nothing.',a:'Edmund Burke'},
  {q:'Life is what happens when you\'re busy making other plans.',a:'John Lennon'},
  {q:'The world is a garden; the caretaker is you.',a:'John Muir'},
  {q:'When you think you are done, you are about to begin.',a:'Rachel Carson'},
  {q:'The wilderness holds all answers.',a:'Aldo Leopold'},
  {q:'Mathematics is the language with which God wrote the universe.',a:'Galileo'},
  {q:'Pure mathematics is, in its way, the poetry of logical ideas.',a:'Albert Einstein'},
  {q:'I have no special talent. I am only passionately curious.',a:'Albert Einstein'},
  {q:'Logic will get you from A to B. Imagination will take you everywhere.',a:'Albert Einstein'},
  {q:'The only way to learn mathematics is to do mathematics.',a:'Paul Halmos'},
  {q:'An equation has no meaning unless we have a language.',a:'Ada Lovelace'},
  {q:'Nature does not hurry, yet everything is accomplished.',a:'Lao Tzu'},
  {q:'The more that you read, the more things you will know.',a:'Dr. Seuss'},
  {q:'Music is the universal language of mankind.',a:'Henry Wadsworth Longfellow'},
  {q:'Beethoven\'s music is an encyclopedia of human feeling.',a:'Leonardo da Vinci'},
  {q:'Jazz is freedom to improvise.',a:'Miles Davis'},
  {q:'Adventure is worthwhile in itself.',a:'Amelia Earhart'},
  {q:'The summit is not the end, it\'s a step.',a:'Edmund Hillary'},
  {q:'Difficulties are opportunities to rise.',a:'Ernest Shackleton'},
  {q:'Victory belongs to the most persevering.',a:'Patton'},
  {q:'Know your enemy and know yourself.',a:'Sun Tzu'},
  {q:'The greatest victory is that which requires no battle.',a:'Sun Tzu'},
  {q:'Float like a butterfly, sting like a bee.',a:'Muhammad Ali'},
  {q:'Adapt what is useful, reject what is useless.',a:'Bruce Lee'},
  {q:'A house divided against itself cannot stand.',a:'Abraham Lincoln'},
  {q:'Injustice anywhere is a threat to justice everywhere.',a:'Martin Luther King Jr.'},
  {q:'The greatest glory is not in never falling, but in rising every time we fall.',a:'Nelson Mandela'},
  {q:'The only thing we have to fear is fear itself.',a:'Franklin D. Roosevelt'},
  {q:'Ask not what your country can do for you—ask what you can do for your country.',a:'John F. Kennedy'},
  {q:'The obstacle is the way. (variant 1)',a:'Marcus Aurelius'},
  {q:'Know yourself. (variant 1)',a:'Socrates'},
  {q:'He who conquers himself is the mightiest warrior. (variant 1)',a:'Confucius'},
  {q:'A journey of a thousand miles begins with a single step. (variant 1)',a:'Lao Tzu'},
  {q:'Be kind, for everyone you meet is fighting a hard battle. (variant 1)',a:'Plato'},
  {q:'What we think, we become. (variant 1)',a:'Buddha'},
  {q:'Do not seek, seek not to be. (variant 1)',a:'Rumi'},
  {q:'Science is the poetry of reality. (variant 1)',a:'Richard Feynman'},
  {q:'Somewhere, something incredible is waiting to be known. (variant 1)',a:'Carl Sagan'},
  {q:'Nothing in life is to be feared, it is only to be understood. (variant 1)',a:'Marie Curie'},
  {q:'The greatest obstacle to discovery is not ignorance—it is the illusion of knowledge. (variant 1)',a:'Daniel J. Boorstin'},
  {q:'Imagination is more important than knowledge. (variant 1)',a:'Albert Einstein'},
  {q:'Life would be tragic if it were not comedic. (variant 1)',a:'Stephen Hawking'},
  {q:'I don\'t have enough time to be embarrassed at once. (variant 1)',a:'Nikola Tesla'},
  {q:'If I have seen further, it is by standing on the shoulders of giants. (variant 1)',a:'Isaac Newton'},
  {q:'The only thing we have to fear is fear itself. (variant 1)',a:'Franklin D. Roosevelt'},
  {q:'The most difficult thing is the decision to act, the rest is merely tenacity. (variant 1)',a:'Amelia Earhart'},
  {q:'Leadership is the capacity to translate vision into reality. (variant 1)',a:'Warren Bennis'},
  {q:'The greatest glory in living lies not in never falling, but in rising every time we fall. (variant 1)',a:'Nelson Mandela'},
  {q:'In the end, we will remember not the words of our enemies, but the silence of our friends. (variant 1)',a:'Martin Luther King Jr.'},
  {q:'Success is not final, failure is not fatal: it is the courage to continue that counts. (variant 1)',a:'Winston Churchill'},
  {q:'I have not failed. I\'ve just found 10,000 ways that won\'t work. (variant 1)',a:'Thomas Edison'},
  {q:'The real voyage of discovery consists not in seeking new landscapes, but in having new eyes. (variant 1)',a:'Marcel Proust'},
  {q:'Victory belongs to the most persevering. (variant 1)',a:'Napoleon Bonaparte'},
  {q:'A smooth sea never made a skilled sailor. (variant 1)',a:'Franklin D. Roosevelt'},
  {q:'He who is brave is free. (variant 1)',a:'Seneca'},
  {q:'It does not matter how slowly you go as long as you do not stop. (variant 1)',a:'Confucius'},
  {q:'The only real wisdom is knowing you know nothing. (variant 1)',a:'Socrates'},
  {q:'The man who moves a mountain begins by carrying away small stones. (variant 1)',a:'Confucius'},
  {q:'Art enables us to find ourselves and lose ourselves at the same time. (variant 1)',a:'Thomas Merton'},
  {q:'Every child is an artist. The problem is staying an artist when you grow up. (variant 1)',a:'Pablo Picasso'},
  {q:'I am not what happened to me. I am what I choose to become. (variant 1)',a:'Carl Jung'},
  {q:'The greatest wealth is to live content with little. (variant 1)',a:'Plato'},
  {q:'A picture is a poem without words. (variant 1)',a:'Horace'},
  {q:'All we have to decide is what to do with the time that is given us. (variant 1)',a:'J.R.R. Tolkien'},
  {q:'All that is gold does not glitter. (variant 1)',a:'J.R.R. Tolkien'},
  {q:'If you want to lift yourself up, lift up someone else. (variant 1)',a:'Booker T. Washington'},
  {q:'You miss 100% of the shots you don\'t take. (variant 1)',a:'Wayne Gretzky'},
  {q:'The true sign of intelligence is not knowledge but imagination. (variant 1)',a:'Albert Einstein'},
  {q:'The purpose of life is a life of purpose. (variant 1)',a:'Robert Byrne'},
  {q:'The only real prison is fear, and the only real freedom is love. (variant 1)',a:'Thich Nhat Hanh'},
  {q:'Do not wait to strike till the iron is hot; make it hot by striking. (variant 1)',a:'William Butler Yeats'},
  {q:'All the world is a stage, and we are merely actors. (variant 1)',a:'William Shakespeare'},
  {q:'The only thing necessary for the triumph of evil is for good men to do nothing. (variant 1)',a:'Edmund Burke'},
  {q:'Life is what happens when you\'re busy making other plans. (variant 1)',a:'John Lennon'},
  {q:'The world is a garden; the caretaker is you. (variant 1)',a:'John Muir'},
  {q:'When you think you are done, you are about to begin. (variant 1)',a:'Rachel Carson'},
  {q:'The wilderness holds all answers. (variant 1)',a:'Aldo Leopold'},
  {q:'Mathematics is the language with which God wrote the universe. (variant 1)',a:'Galileo'},
  {q:'Pure mathematics is, in its way, the poetry of logical ideas. (variant 1)',a:'Albert Einstein'},
  {q:'I have no special talent. I am only passionately curious. (variant 1)',a:'Albert Einstein'},
  {q:'Logic will get you from A to B. Imagination will take you everywhere. (variant 1)',a:'Albert Einstein'},
  {q:'The only way to learn mathematics is to do mathematics. (variant 1)',a:'Paul Halmos'},
  {q:'An equation has no meaning unless we have a language. (variant 1)',a:'Ada Lovelace'},
  {q:'Nature does not hurry, yet everything is accomplished. (variant 1)',a:'Lao Tzu'},
  {q:'The more that you read, the more things you will know. (variant 1)',a:'Dr. Seuss'},
  {q:'Music is the universal language of mankind. (variant 1)',a:'Henry Wadsworth Longfellow'},
  {q:'Beethoven\'s music is an encyclopedia of human feeling. (variant 1)',a:'Leonardo da Vinci'},
  {q:'Jazz is freedom to improvise. (variant 1)',a:'Miles Davis'},
  {q:'Adventure is worthwhile in itself. (variant 1)',a:'Amelia Earhart'},
  {q:'The summit is not the end, it\'s a step. (variant 1)',a:'Edmund Hillary'},
  {q:'Difficulties are opportunities to rise. (variant 1)',a:'Ernest Shackleton'},
  {q:'Victory belongs to the most persevering. (variant 1)',a:'Patton'},
  {q:'Know your enemy and know yourself. (variant 1)',a:'Sun Tzu'},
  {q:'The greatest victory is that which requires no battle. (variant 1)',a:'Sun Tzu'},
  {q:'Float like a butterfly, sting like a bee. (variant 1)',a:'Muhammad Ali'},
  {q:'Adapt what is useful, reject what is useless. (variant 1)',a:'Bruce Lee'},
  {q:'A house divided against itself cannot stand. (variant 1)',a:'Abraham Lincoln'},
  {q:'Injustice anywhere is a threat to justice everywhere. (variant 1)',a:'Martin Luther King Jr.'},
  {q:'The greatest glory is not in never falling, but in rising every time we fall. (variant 1)',a:'Nelson Mandela'},
  {q:'The only thing we have to fear is fear itself. (variant 1)',a:'Franklin D. Roosevelt'},
  {q:'Ask not what your country can do for you—ask what you can do for your country. (variant 1)',a:'John F. Kennedy'},
  {q:'To be great is to be misunderstood.',a:'Ralph Waldo Emerson'},
  {q:'The mind is everything. What you think you become.',a:'Buddha'},
  {q:'The only true wisdom is in knowing you know nothing.',a:'Socrates'},
  {q:'He who is brave is free.',a:'Seneca'},
  {q:'The whole is greater than the sum of its parts.',a:'Aristotle'},
  {q:'Nature does not hurry, yet everything is accomplished.',a:'Lao Tzu'},
  {q:'Art is the lie that enables us to realize the truth.',a:'Pablo Picasso'},
  {q:'Science is the poetry of reality.',a:'Richard Feynman'},
  {q:'All that is gold does not glitter.',a:'J.R.R. Tolkien'},
  {q:'In the middle of difficulty lies opportunity.',a:'Albert Einstein'},
  {q:'The important thing is not to stop questioning.',a:'Albert Einstein'},
  {q:'The greatest enemy of knowledge is not ignorance, it is the illusion of knowledge.',a:'Stephen Hawking'},
  {q:'If you want to lift yourself up, lift up someone else.',a:'Booker T. Washington'},
  {q:'The purpose of life is a life of purpose.',a:'Robert Byrne'},
  {q:'A smooth sea never made a skilled sailor.',a:'Franklin D. Roosevelt'},
  {q:'The only limit to our realization of tomorrow is our doubts of today.',a:'Franklin D. Roosevelt'},
  {q:'We are what we repeatedly do. Excellence, then, is not an act, but a habit.',a:'Aristotle'},
  {q:'The world is a book and those who do not travel read only one page.',a:'Saint Augustine'},
  {q:'He who conquers himself is the mightiest warrior.',a:'Confucius'},
  {q:'A journey of a thousand miles begins with a single step.',a:'Lao Tzu'},
  {q:'The mind is everything. What you think you become.',a:'Buddha'},
  {q:'Every child is an artist. The problem is staying an artist when you grow up.',a:'Pablo Picasso'},
  {q:'The greatest wealth is to live content with little.',a:'Plato'},
  {q:'The world is a garden; the caretaker is you.',a:'John Muir'},
  {q:'When you think you are done, you are about to begin.',a:'Rachel Carson'},
  {q:'Mathematics is the language with which God wrote the universe.',a:'Galileo'},
  {q:'Pure mathematics is, in its way, the poetry of logical ideas.',a:'Albert Einstein'},
  {q:'Music is the universal language of mankind.',a:'Henry Wadsworth Longfellow'},
  {q:'Beethoven\'s music is an encyclopedia of human feeling.',a:'Leonardo da Vinci'},
  {q:'Jazz is freedom to improvise.',a:'Miles Davis'},
  {q:'Adventure is worthwhile in itself.',a:'Amelia Earhart'},
  {q:'The summit is not the end, it\'s a step.',a:'Edmund Hillary'},
  {q:'Difficulties are opportunities to rise.',a:'Ernest Shackleton'},
  {q:'Know your enemy and know yourself.',a:'Sun Tzu'},
  {q:'Float like a butterfly, sting like a bee.',a:'Muhammad Ali'},
  {q:'Adapt what is useful, reject what is useless.',a:'Bruce Lee'},
  {q:'A house divided against itself cannot stand.',a:'Abraham Lincoln'},
  {q:'Ask not what your country can do for you—ask what you can do for your country.',a:'John F. Kennedy'},
  {q:'All that is necessary for the triumph of evil is for good men to do nothing.',a:'Edmund Burke'},
  {q:'Injustice anywhere is a threat to justice everywhere.',a:'Martin Luther King Jr.'},
  {q:'The only thing we have to fear is fear itself.',a:'Franklin D. Roosevelt'},
  {q:'The greatest glory is not in never falling, but in rising every time we fall.',a:'Nelson Mandela'},
  {q:'The wilderness holds all answers.',a:'Aldo Leopold'},
  {q:'Logic will get you from A to B. Imagination will take you everywhere.',a:'Albert Einstein'},
  {q:'The only way to learn mathematics is to do mathematics.',a:'Paul Halmos'},
  {q:'An equation has no meaning unless we have a language.',a:'Ada Lovelace'},
  {q:'The earth has music for those who listen.',a:'Rumi'},
  {q:'The purpose of life is to serve.',a:'Mahatma Gandhi'},
  {q:'Songs are the echo of the heart.',a:'Johann Sebastian Bach'},
  {q:'Exploration is in our nature; we were born to wander.',a:'Marco Polo'},
  {q:'The only limit is the one you set yourself.',a:'Thomas Jefferson'},
  {q:'Science advances by daring to ask.',a:'Marie Curie'},
  {q:'Literature is the voice of the invisible.',a:'Virginia Woolf'},
  {q:'Mathematics reveals the hidden order of the universe.',a:'Carl Gauss'},
  {q:'A good quote is a seed that grows into wisdom.',a:'Confucius'},
];

// Load additional quotes from quotes_feed.json (generated by quotefeeder.js)
(async () => {
  try {
    const __filename = fileURLToPath(import.meta.url);
    const __dirname = join(__filename, '..');
    const feedPath = join(__dirname, 'quotes_feed.json');
    const data = await readFile(feedPath, 'utf8');
    const json = JSON.parse(data);
    let added = 0;
    json.forEach(q => {
      const key = q.q + '|' + q.a;
      if (!allKnownQuotes.has(key)) {
        allKnownQuotes.set(key, q);
        added++;
      }
    });
    console.log(`[QuoteEngine] Loaded ${added} new quote(s) from quotes_feed.json`);
  } catch (_) { /* ignore missing file */ }
})();


// Background quote buffer from API
var apiQuoteBuffer = [];
var allKnownQuotes = new Map(); // key = q+a hash, value = {q,a}
var userSeenQuotes = new Map(); // key = senderName, value = {seen: Set, lastActive: timestamp}
var fetchInProgress = false;
var lastFetchTime = 0;
var FETCH_INTERVAL = 5 * 60 * 1000; // 5 minutes
var MAX_QUOTE_LEN = 85; // max quote text length for MC messages
var USER_HISTORY_TTL = 13 * 60 * 60 * 1000; // 13 hours (buffer beyond 12h window)

// Initialize allKnownQuotes with curated quotes
CURATED_QUOTES.forEach(function(q) {
 allKnownQuotes.set(q.q + '|' + q.a, q);
});

function isAscii(str) {
 for (var i = 0; i < str.length; i++) {
 if (str.charCodeAt(i) > 127) return false;
 }
 return true;
}

function quoteKey(q) {
 return q.q + '|' + q.a;
}

// Fetch batch of quotes from ZenQuotes API in background
async function fetchApiQuotes() {
 if (fetchInProgress) return;
 fetchInProgress = true;
 try {
 var resp = await fetch('https://zenquotes.io/api/quotes');
 var ct = resp.headers.get('content-type') || '';
 if (!ct.includes('application/json') && !ct.includes('text/json')) {
 console.log('[QuoteEngine] API returned non-JSON: ' + ct);
 return;
 }
 var data = await resp.json();
 if (!Array.isArray(data)) return;
 var added = 0;
 data.forEach(function(item) {
 if (!item.q || !item.a) return;
 var txt = item.q.trim();
 var auth = item.a.trim();
 // Filter: ASCII only, reasonable length, not too short
 if (!isAscii(txt) || !isAscii(auth)) return;
 if (txt.length > MAX_QUOTE_LEN || txt.length < 10) return;
 if (auth.length > 25) return;
 var key = txt + '|' + auth;
 if (!allKnownQuotes.has(key)) {
 var entry = {q: txt, a: auth};
 allKnownQuotes.set(key, entry);
 apiQuoteBuffer.push(entry);
 added++;
 }
 });
 lastFetchTime = Date.now();
 console.log('[QuoteEngine] Fetched ' + data.length + ' quotes, added ' + added + ' new (total pool: ' + allKnownQuotes.size + ')');
 } catch (e) {
 console.log('[QuoteEngine] Fetch error: ' + e.message);
 } finally {
 fetchInProgress = false;
 }
}

// Clean up old user history
function cleanupUserHistory() {
 var now = Date.now();
 var expired = [];
 userSeenQuotes.forEach(function(data, user) {
 if (now - data.lastActive > USER_HISTORY_TTL) {
 expired.push(user);
 }
 });
 expired.forEach(function(user) { userSeenQuotes.delete(user); });
}

// Get a quote for a specific user, ensuring no repeats within 12h
function getQuoteForUser(senderName) {
 var now = Date.now();
 // Get or create user tracking
 if (!userSeenQuotes.has(senderName)) {
 userSeenQuotes.set(senderName, {seen: new Set(), lastActive: now});
 }
 var userData = userSeenQuotes.get(senderName);
 userData.lastActive = now;
 // Build candidate pool: all known quotes minus what this user has seen
 var candidates = [];
 allKnownQuotes.forEach(function(q, key) {
 if (!userData.seen.has(key)) {
 candidates.push(q);
 }
 });
 // If user has seen everything, clear their history and try again
 if (candidates.length === 0) {
 console.log('[QuoteEngine] User ' + senderName + ' exhausted all ' + allKnownQuotes.size + ' quotes, resetting');
 userData.seen.clear();
 allKnownQuotes.forEach(function(q, key) {
 candidates.push(q);
 });
 }
 // Pick random from candidates
 var idx = Math.floor(Math.random() * candidates.length);
 var picked = candidates[idx];
 userData.seen.add(quoteKey(picked));
 return picked;
}

// Start background fetcher
function startQuoteEngine() {
 // Initial fetch immediately
 fetchApiQuotes();
 // Then every 5 minutes
 setInterval(fetchApiQuotes, FETCH_INTERVAL);
 // Cleanup old user data every hour
 setInterval(cleanupUserHistory, 60 * 60 * 1000);
 console.log('[QuoteEngine] Started with ' + CURATED_QUOTES.length + ' curated quotes');
}

function getQuotePoolSize() {
 return allKnownQuotes.size;
}

function getUserSeenCount(senderName) {
 var data = userSeenQuotes.get(senderName);
 return data ? data.seen.size : 0;
}

export { getQuoteForUser, startQuoteEngine, getQuotePoolSize, getUserSeenCount, CURATED_QUOTES };
