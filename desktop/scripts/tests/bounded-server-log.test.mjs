import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdtempSync, mkdirSync, rmSync, statSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const repo = dirname(dirname(dirname(dirname(fileURLToPath(import.meta.url)))));
const fixtureRoot = join(repo, '_build');
mkdirSync(fixtureRoot, { recursive: true });
const scratch = mkdtempSync(join(fixtureRoot, 'desktop-logs-test.'));
function put(path, source) { mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, source); }
function run(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: scratch, encoding: 'utf8', timeout: 30000,
    maxBuffer: 16 * 1024 * 1024, env: { ...process.env, VORTX_LOG_TEST_ROOT: scratch }, ...options });
  assert.equal(result.status, 0, `${command} ${args.join(' ')}\n${result.error || ''}\n${result.stdout}\n${result.stderr}`);
  return result;
}
try {
  const files = new Map(['bounded_log.rs', 'server.rs', 'lib.rs'].map(name => [`desktop/src-tauri/src/${name}`, readFileSync(join(repo, 'desktop/src-tauri/src', name), 'utf8')]));
  const bounded = join(scratch, 'bounded_log.rs');
  put(bounded, files.get('desktop/src-tauri/src/bounded_log.rs'));
  run('rustc', ['--edition=2021', '--test', bounded, '-o', join(scratch, 'bounded-log-tests')]);
  const tests = run(join(scratch, 'bounded-log-tests'), ['--test-threads=1']);
  assert.match(tests.stdout, /6 passed; 0 failed; 1 ignored/);
  console.log(tests.stdout.trim());

  const newServer = files.get('desktop/src-tauri/src/server.rs');
  for (const [label, source] of [['after', newServer]]) {
    const home = join(scratch, label); mkdirSync(home);
    const preloadFunction = source.slice(source.indexOf('fn write_preload('), source.indexOf('/// JSON-encode'));
    const unit = join(scratch, `${label}-preload.rs`), executable = join(scratch, `${label}-preload`);
    // Only known ASCII constants use json_string here; the production function itself is exact.
    put(unit, `use std::path::{Path,PathBuf};\nconst HOST:&str="127.0.0.1";\nconst HEALTH_TOKEN_ENV:&str="VORTX_DESKTOP_HEALTH_TOKEN";\nconst HEALTH_PATH:&str="/__vortx_owner_health";\nconst HEALTH_CHALLENGE_HEADER:&str="X-VortX-Health-Challenge";\nfn json_string(s:&str)->String{format!("{:?}",s)}\n${preloadFunction}\nfn main(){write_preload(Path::new(&std::env::args().nth(1).unwrap())).unwrap();}\n`);
    run('rustc', ['--edition=2021', unit, '-o', executable]);
    run(executable, [home]);
    const preload = join(home, 'stremiox-preload.js');
    run(process.execPath, ['--check', preload]);
    const result = run(process.execPath, ['-r', preload, '-e', "for(let i=0;i<1024;i++)process.emit('unhandledRejection','x'.repeat(4096))"]);
      assert(!existsSync(join(home, 'stremio-server.log')), 'preload must not bypass parent file bounds');
      assert(result.stderr.length > 4 * 1024 * 1024, 'synthetic diagnostics must reach the captured stderr pipe');
  }
  assert(!newServer.includes('fs.appendFileSync'));
  assert(newServer.indexOf('log.capture(stdout') < newServer.indexOf('Ok((child, health_token))'));
  assert(newServer.indexOf('log.capture(stderr') < newServer.indexOf('Ok((child, health_token))'));
  console.log('PASS actual preload parses, routes diagnostics to stderr, and cannot bypass the bounded sink');

  const newLib = files.get('desktop/src-tauri/src/lib.rs');
  const outputMethod = newLib.slice(newLib.indexOf('        pub(crate) fn take_output('), newLib.indexOf('        #[cfg(test)]\n        pub(crate) fn id('));
  const spawnFunction = newServer.slice(newServer.indexOf('fn spawn_child('), newServer.indexOf('/// Before the first spawn,'));
  const compileUnit = join(scratch, 'integration.rs');
  put(compileUnit, `#![allow(dead_code)]\nuse std::path::{Path,PathBuf};\nuse std::process::{Command,Stdio};\n#[path=${JSON.stringify(bounded)}]mod bounded_log;\nuse bounded_log::LogSink;\nconst SERVER_LOG_BYTES:u64=2*1024*1024;\nconst HEALTH_TOKEN_ENV:&str="VORTX_DESKTOP_HEALTH_TOKEN";\ntype HealthToken=[u8;32];\nfn fresh_health_token()->std::io::Result<HealthToken>{Ok([0;32])}\nfn write_preload(home:&Path)->std::io::Result<PathBuf>{Ok(home.join("preload.js"))}\nfn hex_encode(_: &[u8])->String{String::new()}\nfn ffmpeg_binaries()->Option<(PathBuf,PathBuf)>{None}\nstruct SupervisedChild{process:std::process::Child}\nimpl SupervisedChild{\nfn command(program:&Path,args:&[String])->std::io::Result<Command>{let mut c=Command::new(program);c.args(args);Ok(c)}\nfn spawn_with_private_env(_:Command,_:&[(&str,&str)])->std::io::Result<Self>{Err(std::io::Error::other("compile-only"))}\n${outputMethod}\n}\n${spawnFunction}\n`);
  run('rustc', ['--edition=2021', '--crate-type=lib', '--emit=metadata', compileUnit, '-o', join(scratch, 'integration.rmeta')]);
  console.log('PASS exact repaired spawn_child and take_output compile with purestd collaborators; no child or app is launched by this check');
} finally { rmSync(scratch, { recursive: true, force: true }); }
