// Chụp ảnh minh chứng (Horizon, Prometheus Targets, Grafana) bằng Chrome headless.
// Cài: npm i puppeteer-core@24.6.0 ; chạy: node docs/screenshot.mjs docs/figures/screens horizon targets grafana-past
// grafana-past cần FROM/TO (epoch ms) của lần chạy muốn chụp.
import puppeteer from 'puppeteer-core';
const out = process.argv[2];
const which = process.argv.slice(3);
const H = 'http://192.168.64.2';
const browser = await puppeteer.launch({
  executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  headless: 'new', args: ['--lang=en-US'],
  defaultViewport: { width: 1440, height: 860, deviceScaleFactor: 2 },
});
const page = await browser.newPage();
const sleep = ms => new Promise(r => setTimeout(r, ms));
const want = w => which.length === 0 || which.includes(w);

async function horizonLogin() {
  await page.goto(`${H}/dashboard/auth/login/`, { waitUntil: 'networkidle2' });
  await page.type('#id_username', 'demo');
  await page.type('#id_password', 'secret');
  await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle2' }), page.click('#loginBtn, button[type=submit]')]);
}
if (want('horizon') || want('topo')) {
  await horizonLogin();
  if (want('horizon')) {
    await page.setViewport({ width: 1440, height: 430, deviceScaleFactor: 2 });
    await page.goto(`${H}/dashboard/project/instances/`, { waitUntil: 'networkidle2' });
    await sleep(1500);
    await page.screenshot({ path: `${out}/horizon-instances.png` });
    await page.setViewport({ width: 1440, height: 860, deviceScaleFactor: 2 });
  }
  if (want('topo')) {
    await page.goto(`${H}/dashboard/project/network_topology/`, { waitUntil: 'networkidle2' });
    await sleep(4000);
    await page.screenshot({ path: `${out}/horizon-topology.png` });
  }
}
if (want('targets')) {
  await page.goto(`${H}:9090/targets`, { waitUntil: 'networkidle2' });
  await sleep(2000);
  await page.click('input[placeholder^="Filter by endpoint"]'); await page.keyboard.type('app-', { delay: 150 });
  await sleep(2500);
  await page.screenshot({ path: `${out}/prometheus-targets.png`, fullPage: true });
}
async function grafana(name, range) {
  await page.goto(`${H}:3000/login`, { waitUntil: 'networkidle2' });
  if (await page.$('input[name=user]')) {
    await page.type('input[name=user]', 'admin');
    await page.type('input[name=password]', 'admin');
    await page.keyboard.press('Enter');
    await sleep(2500);
    const skip = await page.$('button[aria-label="Skip change password button"], a[href$="skip"]');
    if (skip) { await skip.click(); await sleep(1500); }
    else {
      const btns = await page.$$('button');
      for (const b of btns) { const t = await b.evaluate(e => e.textContent.trim()); if (t === 'Skip') { await b.click(); await sleep(1500); break; } }
    }
  }
  await page.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 2 });
  await page.goto(`${H}:3000/d/it5404-autoscale?orgId=1&${range}&kiosk&theme=light`, { waitUntil: 'networkidle2' });
  await sleep(6000);
  await page.screenshot({ path: `${out}/${name}.png` });
}
if (want('grafana')) await grafana('grafana-live', 'from=now-30m&to=now');
if (want('grafana-past')) await grafana('grafana-standard-run', `from=${process.env.FROM}&to=${process.env.TO}`);
await browser.close();
