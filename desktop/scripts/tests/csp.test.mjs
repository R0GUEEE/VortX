import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = dirname(dirname(dirname(dirname(fileURLToPath(import.meta.url)))));
const read = path => readFileSync(join(root, path), 'utf8');
const config = JSON.parse(read('desktop/src-tauri/tauri.conf.json'));
const { csp, devCsp } = config.app.security;
function validate(policy, development = false) {
  assert(policy && typeof policy === 'object');
  for (const key of ['default-src', 'script-src-attr', 'object-src', 'base-uri', 'form-action']) assert.equal(policy[key], "'none'", key);
  assert.equal(policy['worker-src'], development ? "'self' blob:" : "'none'"); // Vite's dev reconnect SharedWorker is blob-backed.
  assert.equal(policy['script-src'], "'self'");
  assert.equal(policy['style-src'], "'self' 'unsafe-inline'"); // Theme custom properties and swatch style attributes.
  assert.equal(policy['style-src-attr'], "'unsafe-inline'"); // Keep attributes working even if Tauri adds a style nonce.
  assert.equal(policy['img-src'], "'self' http: https:");
  assert.equal(policy['media-src'], "'self' http: https:"); // Arbitrary provider URLs remain media, never script/connect authorities.
  assert.equal(policy['font-src'], "'self'");
  assert.equal(policy['frame-src'], 'https://www.youtube-nocookie.com/embed/');
  assert.equal(policy['connect-src'], "'self' ipc: http://ipc.localhost http://127.0.0.1:11470" + (development ? ' http://localhost:1420 ws://localhost:1420' : ''));
  for (const value of Object.values(policy)) assert(!/\*|unsafe-eval|wasm-unsafe-eval/.test(value));
}
test('production CSP admits bundled scripts, IPC, loopback requests and provider media only in their required directives', () => validate(csp));
test('development CSP adds only the fixed Vite HTTP/WebSocket endpoint', () => {
  validate(devCsp, true);
  assert.equal(config.build.devUrl, 'http://localhost:1420');
  assert.match(read('desktop/vite.config.ts'), /port:\s*1420/);
  assert.match(read('desktop/vite.config.ts'), /strictPort:\s*true/);
  for (const key of Object.keys(csp)) if (key !== 'connect-src' && key !== 'worker-src') assert.equal(devCsp[key], csp[key]);
});
test('policy requirements match actual source consumers and local Tauri configuration', () => {
  assert.match(read('desktop/src/detail.ts'), /const TRAILER_HOST = "https:\/\/www\.youtube-nocookie\.com"/);
  assert.match(read('desktop/src/detail.ts'), /\$\{TRAILER_HOST\}\/embed\/\$\{encodeURIComponent\(youtubeId\)\}/);
  assert.match(read('desktop/src/server.ts'), /fetch\(`\$\{base\}\/\$\{hash\}\/create`/);
  assert.match(read('desktop/src-tauri/src/server.rs'), /const HOST: &str = "127\.0\.0\.1"/);
  assert.match(read('desktop/src-tauri/src/server.rs'), /const PORT: u16 = 11470/);
  assert.match(read('desktop/src/player.ts'), /video\.src = url/);
  assert.match(read('desktop/src/settings.ts'), /style="--sw:/);
  assert.notEqual(config.app.security.dangerousDisableAssetCspModification, true);
  for (const platform of ['linux', 'macos', 'windows']) assert(!JSON.parse(read(`desktop/src-tauri/tauri.${platform}.conf.json`)).app?.security, 'platform override must not silently disable CSP');
});
test('null CSP, remote scripts, arbitrary connect/frame origins and release HMR are rejected by contracts', () => {
  for (const policy of [null, { ...csp, 'script-src': "'self' https:" }, { ...csp, 'connect-src': "'self' https:" }, { ...csp, 'frame-src': '*' }, devCsp]) assert.throws(() => validate(policy));
});
