//! The backend and its workers share one process group. The stdin lifetime
//! pipe also lets the backend tear down that group if the desktop app crashes.
use std::{io, process::Child, sync::atomic::{AtomicI32, Ordering}};

pub struct Job(AtomicI32);
impl Job {
    pub fn new() -> io::Result<Self> { Ok(Self(AtomicI32::new(0))) }

    pub fn assign(&self, child: &Child) -> io::Result<()> {
        let pid = i32::try_from(child.id()).map_err(|_| io::Error::other("Invalid child PID"))?;
        // Command::process_group(0) runs before exec, while the start gate keeps
        // Python from doing any work until this ownership check has succeeded.
        if pid <= 1 || unsafe { libc::getpgid(pid) } != pid {
            return Err(io::Error::other("Backend does not own an isolated process group"));
        }
        self.0.store(pid, Ordering::SeqCst);
        Ok(())
    }
}
impl Drop for Job {
    fn drop(&mut self) {
        let group = self.0.swap(0, Ordering::SeqCst);
        if group > 1 { unsafe { libc::kill(-group, libc::SIGKILL); } }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::{BufRead, BufReader}, os::unix::process::CommandExt, process::{Command, Stdio}};
    #[test]
    fn closing_job_kills_the_owned_group() {
        let mut child = Command::new("/bin/sh").args(["-c", "sleep 60 & echo ready; wait"])
            .process_group(0).stdout(Stdio::piped()).spawn().unwrap();
        let job = Job::new().unwrap();
        job.assign(&child).unwrap();
        let mut line = String::new();
        BufReader::new(child.stdout.take().unwrap()).read_line(&mut line).unwrap();
        assert_eq!(line.trim(), "ready");
        drop(job);
        assert!(!child.wait().unwrap().success());
    }
    #[test]
    fn empty_job_never_targets_the_desktop_group() { drop(Job::new().unwrap()); }
}
