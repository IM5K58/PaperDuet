//! One non-inheritable job handle owned by the app. All sidecar descendants
//! inherit job membership. The OS tears them down even if the shell crashes.
use std::{io, os::windows::io::AsRawHandle, process::Child};
use windows_sys::Win32::{
    Foundation::{CloseHandle, HANDLE},
    System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    },
};

pub struct Job(HANDLE);
// The kernel handle is synchronized by Windows; ownership is moved, never cloned.
unsafe impl Send for Job {}

impl Job {
    pub fn new() -> io::Result<Self> {
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() { return Err(io::Error::last_os_error()); }
            let job = Self(handle);
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(handle, JobObjectExtendedLimitInformation,
                &info as *const _ as *const _, std::mem::size_of_val(&info) as u32) == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(job)
        }
    }

    pub fn assign(&self, child: &Child) -> io::Result<()> {
        if unsafe { AssignProcessToJobObject(self.0, child.as_raw_handle() as HANDLE) } == 0 {
            Err(io::Error::last_os_error())
        } else { Ok(()) }
    }
}

impl Drop for Job {
    fn drop(&mut self) { unsafe { CloseHandle(self.0); } }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::{BufRead, BufReader, Write}, process::{Command, Stdio}, thread, time::Duration};
    use windows_sys::Win32::System::Threading::{OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE};
    #[test]
    fn closing_job_kills_child_and_grandchild() {
        // Controlled test processes only. stdin gate prevents a pre-assignment race.
        let script = "$null=[Console]::ReadLine(); $p=Start-Process ping.exe -ArgumentList '-t','127.0.0.1' -WindowStyle Hidden -PassThru; [Console]::WriteLine($p.Id); Start-Sleep 120";
        let mut child = Command::new("powershell.exe").args(["-NoProfile", "-Command", script])
            .stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().unwrap();
        let job = Job::new().unwrap();
        job.assign(&child).unwrap();
        child.stdin.take().unwrap().write_all(b"start\n").unwrap();
        let mut line = String::new();
        BufReader::new(child.stdout.take().unwrap()).read_line(&mut line).unwrap();
        let pid: u32 = line.trim().parse().unwrap();
        let grandchild = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, pid) };
        assert!(!grandchild.is_null());
        drop(job);
        for _ in 0..50 {
            if child.try_wait().unwrap().is_some() { break; }
            thread::sleep(Duration::from_millis(100));
        }
        assert!(child.try_wait().unwrap().is_some());
        assert_eq!(unsafe { WaitForSingleObject(grandchild, 5000) }, 0);
        unsafe { CloseHandle(grandchild); }
    }
}
