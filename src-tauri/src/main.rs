#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(not(windows))]
compile_error!("PaperDuet v1 targets Windows only.");
mod job;
mod sidecar;
mod desktop;
use tauri::Manager;

#[tauri::command]
fn backend_connection(window: tauri::WebviewWindow, state: tauri::State<'_, sidecar::Manager>) -> Result<sidecar::Connection, String> {
    if window.label() != "main" { return Err("Unauthorized window".into()); }
    state.connection()
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let backend = if cfg!(debug_assertions) {
                std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources/backend/paperduet-backend.exe")
            } else {
                app.path().resource_dir()?.join("backend/paperduet-backend.exe")
            };
            let data = if cfg!(debug_assertions) {
                std::env::var_os("PAPERDUET_DATA_DIR").map(std::path::PathBuf::from)
                    .unwrap_or(app.path().data_dir()?.join("PaperDuet"))
            } else { app.path().data_dir()?.join("PaperDuet") };
            let location_file=app.path().data_dir()?.join("PaperDuet-location.json");
            let data=desktop::location(&data,&location_file);
            app.manage(desktop::Desktop{location_file,selected:std::sync::Mutex::new(None),operation:std::sync::Mutex::new(false)});
            desktop::configure_updater(app.handle())?;
            let origin = if cfg!(debug_assertions) { "http://127.0.0.1:1420" } else { "http://tauri.localhost" };
            app.manage(sidecar::Manager::start(backend, data, origin.into()));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_connection,desktop::export_document,desktop::choose_storage,desktop::migrate_storage,desktop::update_status,desktop::check_update,desktop::install_update,desktop::open_link])
        .build(tauri::generate_context!()).expect("Cannot initialize PaperDuet");
    app.run(|handle, event| {
        if matches!(event, tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }) {
            handle.state::<sidecar::Manager>().shutdown();
        }
    });
}
