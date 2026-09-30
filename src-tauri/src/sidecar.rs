use crate::job::Job;
use serde::Serialize;
use std::{
    io::{BufRead, BufReader, Read, Write}, net::{SocketAddr, TcpStream},
    os::windows::process::CommandExt, path::PathBuf, process::{Child, Command, Stdio},
    sync::{atomic::{AtomicBool, Ordering}, mpsc, Arc, Mutex}, thread, time::{Duration, Instant},
};

#[derive(Clone, Serialize)]
pub struct Connection { pub port: u16, pub token: String, pub generation: u32 }

#[derive(Clone)]
pub struct Manager {
    connection: Arc<Mutex<Result<Connection, String>>>,
    stop: Arc<AtomicBool>,
    job: Arc<Mutex<Option<Job>>>,
}

impl Manager {
    pub fn start(exe: PathBuf, data_dir: PathBuf, origin: String) -> Self {
        let manager = Self {
            connection: Arc::new(Mutex::new(Err("로컬 리더를 시작하고 있습니다.".into()))),
            stop: Arc::new(AtomicBool::new(false)), job: Arc::new(Mutex::new(None)),
        };
        let worker = manager.clone();
        thread::spawn(move || worker.supervise(exe, data_dir, origin));
        manager
    }

    pub fn connection(&self) -> Result<Connection, String> {
        self.connection.lock().unwrap().clone()
    }

    pub fn shutdown(&self) {
        self.stop.store(true, Ordering::SeqCst);
        self.job.lock().unwrap().take();
    }

    fn supervise(&self, exe: PathBuf, data_dir: PathBuf, origin: String) {
        // Initial attempt + at most three automatic restarts in one app session.
        for generation in 0..4 {
            if self.stop.load(Ordering::SeqCst) { return; }
            *self.connection.lock().unwrap() = Err("로컬 리더에 연결하고 있습니다.".into());
            if let Ok((mut child, connection)) = self.launch(&exe, &data_dir, &origin, generation) {
                *self.connection.lock().unwrap() = Ok(connection.clone());
                let mut misses = 0;
                let mut next_health = Instant::now() + Duration::from_secs(5);
                while !self.stop.load(Ordering::SeqCst) {
                    if child.try_wait().ok().flatten().is_some() { break; }
                    if Instant::now() >= next_health {
                        misses = if health(&connection, &origin) { 0 } else { misses + 1 };
                        if misses >= 3 { break; }
                        next_health = Instant::now() + Duration::from_secs(5);
                    }
                    thread::sleep(Duration::from_millis(100));
                }
                self.job.lock().unwrap().take();
                let _ = child.kill();
                let _ = child.wait();
            }
            self.job.lock().unwrap().take();
            if self.stop.load(Ordering::SeqCst) { return; }
            thread::sleep(Duration::from_millis(400));
        }
        *self.connection.lock().unwrap() = Err("로컬 리더 시작에 실패했습니다. 앱을 다시 실행해 주세요. (자동 재시작 3회 완료)".into());
    }

    fn launch(&self, exe: &PathBuf, data_dir: &PathBuf, origin: &str, generation: u32)
        -> Result<(Child, Connection), ()> {
        let mut bytes = [0u8; 32];
        getrandom::fill(&mut bytes).map_err(|_| ())?;
        let token: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
        let job = Job::new().map_err(|_| ())?;
        let mut child = Command::new(exe)
            .current_dir(exe.parent().ok_or(())?)
            .env("PAPERDUET_SESSION_TOKEN", &token).env("PAPERDUET_ORIGIN", origin)
            .env("PAPERDUET_DATA_DIR", data_dir).env("PAPERDUET_START_GATE", "1")
            .env("PYTHONUTF8", "1").env("PYTHONIOENCODING", "utf-8")
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
            .creation_flags(0x08000000) // CREATE_NO_WINDOW
            .spawn().map_err(|_| ())?;
        if job.assign(&child).is_err() {
            let _ = child.kill(); let _ = child.wait(); return Err(());
        }
        {
            let mut slot = self.job.lock().unwrap();
            if self.stop.load(Ordering::SeqCst) { drop(job); let _ = child.wait(); return Err(()); }
            *slot = Some(job);
        }
        let result = (|| {
            child.stdin.take().ok_or(())?.write_all(b"start\n").map_err(|_| ())?;
            let output = child.stdout.take().ok_or(())?;
            let (send, receive) = mpsc::channel();
            thread::spawn(move || {
                let mut line = String::new();
                let result = BufReader::new(output).take(1024).read_line(&mut line);
                if result.is_ok() { let _ = send.send(line); }
            });
            let deadline = Instant::now() + Duration::from_secs(30);
            let line = loop {
                if self.stop.load(Ordering::SeqCst) || Instant::now() >= deadline { return Err(()); }
                match receive.recv_timeout(Duration::from_millis(100)) {
                    Ok(line) => break line,
                    Err(mpsc::RecvTimeoutError::Timeout) => continue,
                    Err(_) => return Err(()),
                }
            };
            let value: serde_json::Value = serde_json::from_str(&line).map_err(|_| ())?;
            if value["event"] != "ready" { return Err(()); }
            let port = value["port"].as_u64().filter(|p| *p > 0 && *p <= 65535).ok_or(())? as u16;
            let connection = Connection { port, token, generation };
            if !health(&connection, origin) { return Err(()); }
            Ok(connection)
        })();
        match result {
            Ok(connection) => Ok((child, connection)),
            Err(()) => { self.job.lock().unwrap().take(); let _ = child.kill(); let _ = child.wait(); Err(()) }
        }
    }
}

fn health(connection: &Connection, origin: &str) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], connection.port));
    let Ok(mut socket) = TcpStream::connect_timeout(&address, Duration::from_secs(2)) else { return false; };
    let _ = socket.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = socket.set_write_timeout(Some(Duration::from_secs(2)));
    let request = format!("GET /health HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nOrigin: {}\r\nAuthorization: Bearer {}\r\nConnection: close\r\n\r\n", connection.port, origin, connection.token);
    if socket.write_all(request.as_bytes()).is_err() { return false; }
    let mut response = String::new();
    socket.take(4096).read_to_string(&mut response).is_ok() && response.starts_with("HTTP/1.1 200 ")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn packaged_backend_restart_and_shutdown() {
        let Some(exe) = std::env::var_os("PAPERDUET_TEST_SIDECAR") else {
            eprintln!("Set PAPERDUET_TEST_SIDECAR to run the packaged sidecar integration test"); return;
        };
        let data = std::env::temp_dir().join(format!("PaperDuet 테스트 {}", std::process::id()));
        let manager = Manager::start(PathBuf::from(exe), data, "http://tauri.localhost".into());
        let ready = |generation| {
            let start = Instant::now();
            loop {
                if let Ok(c) = manager.connection() {
                    if c.generation == generation { return c; }
                }
                assert!(start.elapsed() < Duration::from_secs(40), "Sidecar never became ready");
                thread::sleep(Duration::from_millis(100));
            }
        };
        let first = ready(0);
        assert!(health(&first, "http://tauri.localhost"));
        assert!(!health(&first, "https://example.invalid"));
        manager.job.lock().unwrap().take(); // Simulate backend crash.
        let second = ready(1);
        assert_ne!(first.token, second.token);
        manager.job.lock().unwrap().take();
        let third = ready(2);
        assert_ne!(second.token, third.token);
        manager.job.lock().unwrap().take();
        let fourth = ready(3);
        assert_ne!(third.token, fourth.token);
        manager.shutdown();
        let deadline = Instant::now() + Duration::from_secs(5);
        while health(&fourth, "http://tauri.localhost") {
            assert!(Instant::now() < deadline, "Backend remained after shutdown");
            thread::sleep(Duration::from_millis(100));
        }
    }
}
