const http = require('http');
const { chromium } = require('playwright-core');

const PORT = parseInt(process.env.PORT || '3000', 10);

const BROWSER_ARGS = [
  '--disable-dev-shm-usage',
  '--no-sandbox',
  '--disable-gpu',
  '--single-process',
  '--disable-setuid-sandbox',
  '--disable-background-timer-throttling',
  '--disable-backgrounding-occluded-windows',
  '--disable-renderer-backgrounding',
];

let browser;

async function getBrowser() {
  if (!browser || !browser.isConnected()) {
    browser = await chromium.launch({ args: BROWSER_ARGS });
  }
  return browser;
}

function parseBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', chunk => { data += chunk; });
    req.on('end', () => {
      try { resolve(JSON.parse(data || '{}')); }
      catch (e) { reject(new Error('Invalid JSON')); }
    });
    req.on('error', reject);
  });
}

function send(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(payload),
  });
  res.end(payload);
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    return send(res, 200, { status: 'ok' });
  }

  if (req.method === 'POST' && req.url === '/render') {
    let body;
    try {
      body = await parseBody(req);
    } catch {
      return send(res, 400, { error: 'Invalid JSON body' });
    }

    const { url, waitFor } = body;
    if (!url) return send(res, 400, { error: '`url` is required' });

    let page;
    try {
      const b = await getBrowser();
      page = await b.newPage();

      const waitUntil = waitFor === 'networkidle' ? 'networkidle' : 'load';
      await page.goto(url, { waitUntil, timeout: 30000 });

      const html = await page.content();
      return send(res, 200, { html });
    } catch (err) {
      return send(res, 500, { error: err.message });
    } finally {
      if (page) await page.close().catch(() => {});
    }
  }

  return send(res, 404, { error: 'Not found' });
});

server.listen(PORT, () => {
  console.log(`Playwright service listening on :${PORT}`);
});

async function shutdown() {
  if (browser) await browser.close().catch(() => {});
  server.close(() => process.exit(0));
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
