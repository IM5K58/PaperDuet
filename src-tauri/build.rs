fn main() {
    println!("cargo:rerun-if-env-changed=PAPERDUET_UPDATE_URL");
    println!("cargo:rerun-if-env-changed=PAPERDUET_UPDATE_PUBLIC_KEY");
    tauri_build::build()
}
