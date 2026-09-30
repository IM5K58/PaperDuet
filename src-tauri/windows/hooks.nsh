!macro NSIS_HOOK_PREUNINSTALL
  ; Default is preservation, including silent uninstall. Updates preserve data.
  IfSilent paperduet_keep_data
  IfFileExists "$APPDATA\PaperDuet\paperduet.sqlite3" 0 paperduet_keep_data
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "PaperDuet 사용자 데이터(논문·주석·읽기 위치)를 삭제할까요? 기본값은 보존입니다." IDNO paperduet_keep_data
  RMDir /r "$APPDATA\PaperDuet"
  paperduet_keep_data:
!macroend
