use std::{path::{Path,PathBuf},sync::Mutex,time::Duration,io::Write};
use serde_json::{json,Value};
use tauri::State;
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_updater::UpdaterExt;
use crate::sidecar;

pub struct Desktop {pub location_file:PathBuf,pub selected:Mutex<Option<PathBuf>>,pub operation:Mutex<bool>}
pub fn location(default:&Path,config:&Path)->PathBuf{
    std::fs::read(config).ok().and_then(|b|serde_json::from_slice::<Value>(&b).ok())
        .and_then(|v|v["path"].as_str().map(PathBuf::from)).filter(|p|p.is_absolute()).unwrap_or(default.to_path_buf())
}
fn allowed(window:&tauri::WebviewWindow)->Result<(),String>{if window.label()=="main"{Ok(())}else{Err("허용되지 않은 창입니다.".into())}}
fn valid_document_id(id:&str)->bool{
    !id.is_empty()&&!matches!(id,"."|"..")&&id.bytes().all(|b|b.is_ascii_alphanumeric()||matches!(b,b'-'|b'_'|b'.'))
}
fn client()->Result<reqwest::Client,String>{reqwest::Client::builder().no_proxy().redirect(reqwest::redirect::Policy::none()).timeout(Duration::from_secs(900)).build().map_err(|_|"로컬 연결을 준비하지 못했습니다.".into())}
fn local(connection:&sidecar::Connection,path:&str)->String{format!("http://127.0.0.1:{}{}",connection.port,path)}
fn atomic_write(path:&Path,bytes:&[u8])->Result<(),String>{
    let parent=path.parent().ok_or("저장 폴더가 없습니다.")?;
    std::fs::create_dir_all(parent).map_err(|_|"저장 폴더를 만들지 못했습니다.")?;
    let mut file=tempfile::NamedTempFile::new_in(parent).map_err(|_|"임시 파일을 만들지 못했습니다.")?;
    file.write_all(bytes).and_then(|_|file.as_file().sync_all()).map_err(|_|"파일 저장에 실패했습니다.")?;
    file.persist(path).map_err(|_|"파일 교체에 실패했습니다.")?;Ok(())
}

#[tauri::command]
pub async fn export_document(window:tauri::WebviewWindow,app:tauri::AppHandle,state:State<'_,sidecar::Manager>,doc_id:String,format:String)->Result<Option<String>,String>{
    allowed(&window)?;
    if !valid_document_id(&doc_id)||!matches!(format.as_str(),"html"|"md"|"presentation"){return Err("올바르지 않은 내보내기 요청입니다.".into());}
    let ext=if format=="html"{"html"}else{"md"};let filename=format!("{doc_id}{}.{}",if format=="presentation"{"-presentation"}else{""},ext);
    let path=tauri::async_runtime::spawn_blocking(move||app.dialog().file().set_title("논문 내보내기").set_file_name(filename).add_filter("PaperDuet",&[ext]).blocking_save_file()).await.map_err(|_|"저장 창을 열지 못했습니다.")?;
    let Some(path)=path else{return Ok(None)};let path=path.into_path().map_err(|_|"파일 경로를 확인해 주세요.")?;
    let connection=state.connection()?;
    let response=client()?.get(local(&connection,&format!("/documents/{doc_id}/export?format={format}"))).bearer_auth(&connection.token).send().await.map_err(|_|"내보내기 요청에 실패했습니다.")?;
    if !response.status().is_success(){return Err("내보낼 파일을 준비하지 못했습니다.".into());}
    let bytes=response.bytes().await.map_err(|_|"내보내기를 받지 못했습니다.")?;
    atomic_write(&path,&bytes)?;Ok(Some(path.to_string_lossy().into_owned()))
}

#[tauri::command]
pub async fn choose_storage(window:tauri::WebviewWindow,app:tauri::AppHandle,desktop:State<'_,Desktop>)->Result<Option<String>,String>{
    allowed(&window)?;
    let path=tauri::async_runtime::spawn_blocking(move||app.dialog().file().set_title("비어 있는 새 저장 폴더").blocking_pick_folder()).await.map_err(|_|"폴더 선택 창을 열지 못했습니다.")?;
    let path=path.map(|p|p.into_path()).transpose().map_err(|_|"폴더 경로를 확인해 주세요.")?;
    *desktop.selected.lock().unwrap()=path.clone();Ok(path.map(|p|p.to_string_lossy().into_owned()))
}

#[tauri::command]
pub async fn migrate_storage(window:tauri::WebviewWindow,app:tauri::AppHandle,state:State<'_,sidecar::Manager>,desktop:State<'_,Desktop>,path:String)->Result<(),String>{
    allowed(&window)?;
    if desktop.selected.lock().unwrap().as_ref()!=Some(&PathBuf::from(&path)){return Err("먼저 새 폴더를 선택해 주세요.".into());}
    {let mut lock=desktop.operation.lock().unwrap();if *lock{return Err("다른 작업을 마친 뒤 다시 시도해 주세요.".into())}*lock=true;}
    let result=async {
        let connection=state.connection()?;
        let response=client()?.post(local(&connection,"/settings/storage/migrate")).bearer_auth(&connection.token).json(&json!({"path":path})).send().await.map_err(|_|"저장 위치 복사 중 연결이 끊겼습니다. 기존 데이터는 유지됩니다.")?;
        if !response.status().is_success(){let code=response.json::<Value>().await.unwrap_or_default();return Err(match code["detail"].as_str(){Some("STORAGE_BUSY")=>"번역·질문이 끝난 뒤 다시 시도해 주세요.",Some("STORAGE_NOT_EMPTY")=>"비어 있는 폴더를 선택해 주세요.",Some("STORAGE_NO_SPACE")=>"저장 공간이 부족합니다.",_=>"복사하지 못했습니다. 경로·권한을 확인해 주세요. 기존 데이터는 유지됩니다."}.into());}
        let verified=response.json::<Value>().await.map_err(|_|"복사 결과를 확인하지 못했습니다.")?;
        if verified["verified"]!=true{return Err("복사 검증에 실패했습니다.".into());}
        let config=serde_json::to_vec(&json!({"path":path})).map_err(|_|"저장 설정 오류")?;
        atomic_write(&desktop.location_file,&config)?;
        state.shutdown();app.restart();
    }.await;
    if result.is_err(){
        if let (Ok(connection),Ok(client))=(state.connection(),client()){
            let _=client.post(local(&connection,"/settings/storage/cancel")).bearer_auth(&connection.token).send().await;
        }
    }
    *desktop.operation.lock().unwrap()=false;result
}

fn update_config()->Option<(&'static str,&'static str)>{
    let url=option_env!("PAPERDUET_UPDATE_URL")?;let key=option_env!("PAPERDUET_UPDATE_PUBLIC_KEY")?;
    if url.starts_with("https://")&&!key.is_empty(){Some((url,key))}else{None}
}
pub fn configure_updater(app:&tauri::AppHandle)->tauri::Result<()> {
    if let Some((_,key))=update_config(){app.plugin(tauri_plugin_updater::Builder::new().pubkey(key).build())?;}Ok(())
}
#[tauri::command]
pub fn update_status(window:tauri::WebviewWindow)->Result<Value,String>{allowed(&window)?;Ok(json!({"configured":update_config().is_some(),"current_version":env!("CARGO_PKG_VERSION")}))}
#[tauri::command]
pub async fn check_update(window:tauri::WebviewWindow,app:tauri::AppHandle)->Result<Value,String>{
    allowed(&window)?;
    let Some((url,key))=update_config() else{return update_status(window)};
    let update=app.updater_builder().pubkey(key).endpoints(vec![url.parse().map_err(|_|"업데이트 주소 오류")?]).map_err(|_|"업데이트 설정 오류")?.timeout(Duration::from_secs(30)).build().map_err(|_|"업데이트 설정 오류")?.check().await.map_err(|_|"업데이트 확인에 실패했습니다. 네트워크를 확인해 주세요.")?;
    Ok(match update {Some(u)=>json!({"configured":true,"available":true,"current_version":env!("CARGO_PKG_VERSION"),"version":u.version,"body":u.body}),None=>json!({"configured":true,"available":false,"current_version":env!("CARGO_PKG_VERSION")})})
}
#[tauri::command]
pub async fn install_update(window:tauri::WebviewWindow,app:tauri::AppHandle,state:State<'_,sidecar::Manager>,desktop:State<'_,Desktop>)->Result<(),String>{
    allowed(&window)?;let Some((url,key))=update_config() else{return Err("업데이트 배포 경로가 연결되지 않았습니다.".into())};
    {let mut lock=desktop.operation.lock().unwrap();if *lock{return Err("다른 작업을 마친 뒤 다시 시도해 주세요.".into())}*lock=true;}
    let result=async{
        let connection=state.connection()?;
        let status=client()?.get(local(&connection,"/settings/storage")).bearer_auth(&connection.token).send().await.map_err(|_|"앱 상태 확인 실패")?.json::<Value>().await.map_err(|_|"앱 상태 확인 실패")?;
        if status["busy"]==true{return Err("번역·질문을 마친 뒤 업데이트해 주세요.".into());}
        let manager=state.inner().clone();
        let updater=app.updater_builder().pubkey(key).endpoints(vec![url.parse().map_err(|_|"업데이트 주소 오류")?]).map_err(|_|"업데이트 설정 오류")?.on_before_exit(move||manager.shutdown()).timeout(Duration::from_secs(120)).build().map_err(|_|"업데이트 설정 오류")?;
        let Some(update)=updater.check().await.map_err(|_|"업데이트 확인에 실패했습니다.")? else{return Err("현재 최신 버전입니다.".into())};
        update.download_and_install(|_,_|{},||{}).await.map_err(|_|"업데이트 다운로드·서명 검증·설치에 실패했습니다. 기존 앱은 유지됩니다.".into())
    }.await;
    *desktop.operation.lock().unwrap()=false;result
}

#[cfg(test)]
mod tests{
    use super::*;
    #[test]fn export_accepts_arxiv_ids_without_path_segments(){
        for id in ["rex-omni","arxiv-2603.07952v1","arxiv-hep-th-9901001","paper_1"]{assert!(valid_document_id(id));}
        for id in ["",".","..","../secret","a/b",r"a\b","x?format=md","x#fragment"]{assert!(!valid_document_id(id));}
    }
    #[test]fn location_config_and_atomic_replace(){
        let dir=tempfile::tempdir().unwrap();let config=dir.path().join("위치 설정.json");let default=dir.path().join("기본 폴더");let target=dir.path().join("새 논문 폴더");
        assert_eq!(location(&default,&config),default);
        atomic_write(&config,&serde_json::to_vec(&json!({"path":target})).unwrap()).unwrap();assert_eq!(location(&default,&config),target);
        atomic_write(&config,b"{}").unwrap();assert_eq!(location(&default,&config),default);
    }
}
