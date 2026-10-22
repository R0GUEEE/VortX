//! Bounded streaming child diagnostics. No unbounded queue or child-output buffer is retained.

use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock, Weak};
use std::thread::JoinHandle;

type SharedLog = Arc<Mutex<RotatingLog>>;
type LogRegistry = Mutex<HashMap<PathBuf, Weak<Mutex<RotatingLog>>>>;
static LOGS: OnceLock<LogRegistry> = OnceLock::new();

/// Generations of the same child share one writer until their output pipes have reached EOF.
/// This prevents a restart's newly opened handle from racing an old drainer during rotation.
#[derive(Clone)]
pub(crate) struct LogSink(SharedLog);

impl LogSink {
    pub(crate) fn open(path: &Path, cap: u64) -> io::Result<Self> {
        if cap == 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "log cap is zero",
            ));
        }
        let name = path.file_name().ok_or_else(|| {
            io::Error::new(io::ErrorKind::InvalidInput, "log path has no filename")
        })?;
        let parent = path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        let path = fs::canonicalize(parent)?.join(name);
        let mut registry = LOGS
            .get_or_init(|| Mutex::new(HashMap::new()))
            .lock()
            .map_err(|_| io::Error::other("log registry poisoned"))?;
        registry.retain(|_, log| log.strong_count() != 0);
        if let Some(log) = registry.get(&path).and_then(Weak::upgrade) {
            if log
                .lock()
                .map_err(|_| io::Error::other("log sink poisoned"))?
                .cap
                != cap
            {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "log cap changed while open",
                ));
            }
            return Ok(Self(log));
        }
        let log = Arc::new(Mutex::new(RotatingLog::open(path.clone(), cap)?));
        registry.insert(path, Arc::downgrade(&log));
        Ok(Self(log))
    }

    /// Always consume the pipe through EOF, even if the disk fills or logging fails. Otherwise a
    /// failed diagnostic write could fill the OS pipe and deadlock an otherwise healthy server.
    pub(crate) fn capture<R: Read + Send + 'static>(
        &self,
        mut reader: R,
        name: &str,
    ) -> io::Result<JoinHandle<()>> {
        let log = Arc::clone(&self.0);
        std::thread::Builder::new()
            .name(name.to_owned())
            .spawn(move || {
                let mut buffer = [0u8; 8192];
                let mut logging = true;
                loop {
                    let size = match reader.read(&mut buffer) {
                        Ok(0) => break,
                        Ok(size) => size,
                        Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                        Err(_) => break,
                    };
                    if logging {
                        let result = log
                            .lock()
                            .map_err(|_| io::Error::other("log sink poisoned"))
                            .and_then(|mut log| log.append(&buffer[..size]));
                        if let Err(error) = result {
                            eprintln!("stremiox: server log capture disabled: {error}");
                            logging = false;
                        }
                    }
                }
            })
    }
}

struct RotatingLog {
    path: PathBuf,
    backup: PathBuf,
    file: Option<File>,
    len: u64,
    cap: u64,
}

impl RotatingLog {
    fn open(path: PathBuf, cap: u64) -> io::Result<Self> {
        let mut backup_name = path.as_os_str().to_os_string();
        backup_name.push(".1");
        let backup = PathBuf::from(backup_name);
        cap_existing(&backup, cap)?;
        cap_existing(&path, cap)?;
        let file = OpenOptions::new().create(true).append(true).open(&path)?;
        let len = file.metadata()?.len();
        Ok(Self {
            path,
            backup,
            file: Some(file),
            len,
            cap,
        })
    }

    fn append(&mut self, mut bytes: &[u8]) -> io::Result<()> {
        if self.file.is_none() {
            return Err(io::Error::other("log writer unavailable"));
        }
        while !bytes.is_empty() {
            if self.len == self.cap {
                // Close before rename so rotation works on Windows as well as Unix.
                self.file.take();
                match fs::remove_file(&self.backup) {
                    Ok(()) => {}
                    Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                    Err(error) => return Err(error),
                }
                fs::rename(&self.path, &self.backup)?;
                self.file = Some(
                    OpenOptions::new()
                        .create_new(true)
                        .append(true)
                        .open(&self.path)?,
                );
                self.len = 0;
            }
            let size = bytes
                .len()
                .min((self.cap - self.len).min(usize::MAX as u64) as usize);
            if let Err(error) = self
                .file
                .as_mut()
                .ok_or_else(|| io::Error::other("log writer unavailable"))?
                .write_all(&bytes[..size])
            {
                // A partial write must disable the shared writer too: its length is no longer
                // known, and the other stream must never append against an underestimated cap.
                self.file.take();
                return Err(error);
            }
            self.len += size as u64;
            bytes = &bytes[size..];
        }
        Ok(())
    }
}

/// Bound retained logs from older versions too. Shift only the final `cap` bytes in fixed chunks,
/// not a file-sized allocation. Reject symlinks/non-files rather than truncate their targets.
fn cap_existing(path: &Path, cap: u64) -> io::Result<()> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    if !metadata.file_type().is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "log path is not a regular file",
        ));
    }
    if metadata.len() <= cap {
        return Ok(());
    }
    let mut file = OpenOptions::new().read(true).write(true).open(path)?;
    let start = metadata.len() - cap;
    let mut copied = 0;
    let mut buffer = [0u8; 8192];
    while copied < cap {
        let size = (cap - copied).min(buffer.len() as u64) as usize;
        file.seek(SeekFrom::Start(start + copied))?;
        file.read_exact(&mut buffer[..size])?;
        file.seek(SeekFrom::Start(copied))?;
        file.write_all(&buffer[..size])?;
        copied += size as u64;
    }
    file.set_len(cap)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static NEXT: AtomicUsize = AtomicUsize::new(0);
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let root = PathBuf::from(
                std::env::var_os("VORTX_LOG_TEST_ROOT")
                    .expect("tests require an explicit workspace fixture root"),
            );
            let path = root.join(format!(
                "log-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
        fn log(&self) -> PathBuf {
            self.0.join("server.log")
        }
        fn backup(&self) -> PathBuf {
            self.0.join("server.log.1")
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn caps_streams_larger_than_one_rotation_and_preserves_latest_tail() {
        let fixture = Fixture::new();
        let sink = LogSink::open(&fixture.log(), 64).unwrap();
        let data: Vec<u8> = (0..1000).map(|n| (n % 251) as u8).collect();
        sink.capture(io::Cursor::new(data.clone()), "rotation-test")
            .unwrap()
            .join()
            .unwrap();
        let active = fs::read(fixture.log()).unwrap();
        let backup = fs::read(fixture.backup()).unwrap();
        assert!(active.len() <= 64 && backup.len() <= 64);
        assert_eq!([backup, active].concat(), data[data.len() - 104..]);
    }

    #[test]
    fn bounds_prior_active_and_backup_without_losing_tail() {
        let fixture = Fixture::new();
        let data: Vec<u8> = (0..20000).map(|n| (n % 251) as u8).collect();
        fs::write(fixture.log(), &data).unwrap();
        fs::write(fixture.backup(), &data).unwrap();
        let _sink = LogSink::open(&fixture.log(), 12345).unwrap();
        assert_eq!(fs::read(fixture.log()).unwrap(), data[data.len() - 12345..]);
        assert_eq!(
            fs::read(fixture.backup()).unwrap(),
            data[data.len() - 12345..]
        );
    }

    #[test]
    fn restarts_share_the_same_writer_and_reject_cap_changes() {
        let fixture = Fixture::new();
        let a = LogSink::open(&fixture.log(), 64).unwrap();
        let b = LogSink::open(&fixture.log(), 64).unwrap();
        assert!(Arc::ptr_eq(&a.0, &b.0));
        assert!(LogSink::open(&fixture.log(), 65).is_err());
        assert!(LogSink::open(&fixture.0.join("zero.log"), 0).is_err());
    }

    struct Counted {
        consumed: Arc<AtomicUsize>,
        left: usize,
    }
    impl Read for Counted {
        fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
            let size = output.len().min(self.left);
            output[..size].fill(b'x');
            self.left -= size;
            self.consumed.fetch_add(size, Ordering::Relaxed);
            Ok(size)
        }
    }
    #[test]
    fn logging_failure_still_drains_all_output() {
        let fixture = Fixture::new();
        let sink = LogSink::open(&fixture.log(), 16).unwrap();
        fs::create_dir(fixture.backup()).unwrap(); // force rotation failure without platform tricks
        let consumed = Arc::new(AtomicUsize::new(0));
        sink.capture(
            Counted {
                consumed: Arc::clone(&consumed),
                left: 100000,
            },
            "failed-log-test",
        )
        .unwrap()
        .join()
        .unwrap();
        assert_eq!(consumed.load(Ordering::Relaxed), 100000);
        assert_eq!(fs::metadata(fixture.log()).unwrap().len(), 16);
    }

    #[test]
    fn rejects_non_file_and_symlink_logs() {
        let fixture = Fixture::new();
        fs::create_dir(fixture.log()).unwrap();
        assert!(LogSink::open(&fixture.log(), 64).is_err());
        #[cfg(unix)]
        {
            let target = fixture.0.join("target");
            fs::write(&target, b"untouched").unwrap();
            let link = fixture.0.join("linked.log");
            std::os::unix::fs::symlink(&target, &link).unwrap();
            assert!(LogSink::open(&link, 64).is_err());
            assert_eq!(fs::read(target).unwrap(), b"untouched");
        }
    }

    #[test]
    fn drains_both_real_child_pipes_without_deadlocking_or_growing_files() {
        let fixture = Fixture::new();
        let sink = LogSink::open(&fixture.log(), 65536).unwrap();
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--ignored",
                "--exact",
                "bounded_log::tests::output_fixture",
                "--nocapture",
            ])
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .unwrap();
        let out = sink
            .capture(child.stdout.take().unwrap(), "test-out")
            .unwrap();
        let err = sink
            .capture(child.stderr.take().unwrap(), "test-err")
            .unwrap();
        assert!(child.wait().unwrap().success());
        out.join().unwrap();
        err.join().unwrap();
        assert!(fs::metadata(fixture.log()).unwrap().len() <= 65536);
        assert!(fs::metadata(fixture.backup()).unwrap().len() <= 65536);
        let tail = [
            fs::read(fixture.backup()).unwrap(),
            fs::read(fixture.log()).unwrap(),
        ]
        .concat();
        assert!(tail.windows(11).any(|bytes| bytes == b"STDERR-END!"));
        assert!(tail.windows(11).any(|bytes| bytes == b"STDOUT-END!"));
    }

    #[test]
    #[ignore]
    fn output_fixture() {
        let mut out = io::stdout().lock();
        let mut err = io::stderr().lock();
        for _ in 0..256 {
            out.write_all(&[b'O'; 8192]).unwrap();
            err.write_all(&[b'E'; 8192]).unwrap();
        }
        out.write_all(b"STDOUT-END!").unwrap();
        out.flush().unwrap();
        err.write_all(b"STDERR-END!").unwrap();
        err.flush().unwrap();
    }
}
