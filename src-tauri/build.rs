fn main() {
    // Windows stores the title-bar/taskbar icon in the executable resources.
    // Cargo must rerun the resource compiler when the artwork changes.
    println!("cargo:rerun-if-changed=icons/icon.ico");
    println!("cargo:rerun-if-changed=icons/icon.png");
    println!("cargo:rerun-if-env-changed=PAPERDUET_UPDATE_URL");
    println!("cargo:rerun-if-env-changed=PAPERDUET_UPDATE_PUBLIC_KEY");
    tauri_build::build()
}
