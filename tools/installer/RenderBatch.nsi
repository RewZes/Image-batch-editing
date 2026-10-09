; RenderBatch installer (NSIS 3). Build with tools/build_installer.py, which passes:
;   /DVERSION=3.8.0  /DSRC=<folder with the app core>  /DOUTFILE=<RenderBatch_Setup.exe>  /DICON=<icon.ico>
;
; Two ways to install:
;   Install  - into your user folder (no admin rights needed), Start menu + optional desktop shortcut,
;              listed in Windows Settings > Apps with an uninstaller.
;   Portable - everything in one folder you pick (e.g. a USB drive); nothing is written outside it.
; The Python packages and big models come from the RenderBatch_*.rbdata files next to the installer (or in
; Downloads), or are copied from a RenderBatch folder already on this PC (app\start.py --setup-only).

Unicode true
Target amd64-unicode           ; RenderBatch is 64-bit only
ManifestDPIAware true
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64

!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "WinMessages.nsh"

!define APP "RenderBatch"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"
!define MUTEX "RenderBatch_running"

Name "${APP} ${VERSION}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\${APP}"
BrandingText "${APP} ${VERSION}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1033 "ProductName" "${APP}"
VIAddVersionKey /LANG=1033 "FileDescription" "${APP} setup"
VIAddVersionKey /LANG=1033 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "${APP}"

Var Mode            ; installed | portable
Var WantDesktop
Var WantStart
Var Existing        ; folder of an installed copy (from the registry), or empty
Var ExistingVer
Var UserPickedDir
Var RadInstall
Var RadPortable
Var ChkDesktop
Var ChkStart
Var SetupOk

!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_ABORTWARNING
!define MUI_LANGDLL_ALLLANGUAGES

!insertmacro MUI_PAGE_WELCOME
Page custom TypePage TypeLeave
!define MUI_PAGE_CUSTOMFUNCTION_PRE DirPre
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE DirLeave
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\RenderBatch.exe"
!define MUI_FINISHPAGE_RUN_TEXT "$(T_RUN)"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Russian"
!insertmacro MUI_LANGUAGE "Romanian"
!insertmacro MUI_RESERVEFILE_LANGDLL

!include /CHARSET=UTF8 "strings.nsh"

; ---------------------------------------------------------------------------------------------- helpers

!macro CHECK_RUNNING
  ${Do}
    System::Call 'kernel32::OpenMutexW(i 0x00100000, i 0, w "${MUTEX}") p .r9'
    ${If} $9 == 0
      ${Break}
    ${EndIf}
    System::Call 'kernel32::CloseHandle(p r9)'
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$(T_RUNNING)" /SD IDCANCEL IDRETRY +2
    Abort
  ${Loop}
!macroend

Function .onInit
  !insertmacro MUI_LANGDLL_DISPLAY
  StrCpy $Mode "installed"
  StrCpy $WantDesktop "1"
  StrCpy $WantStart "1"
  StrCpy $UserPickedDir ""
  ; silent / scripted installs: Setup.exe /S [/PORTABLE] [/NODESKTOP] [/D=folder]
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/PORTABLE" $R1
  ${IfNot} ${Errors}
    StrCpy $Mode "portable"
    StrCpy $WantDesktop "0"
    StrCpy $WantStart "0"
  ${EndIf}
  ClearErrors
  ${GetOptions} $R0 "/NODESKTOP" $R1
  ${IfNot} ${Errors}
    StrCpy $WantDesktop "0"
  ${EndIf}
  ReadRegStr $Existing HKCU "${UNINST_KEY}" "InstallLocation"
  ReadRegStr $ExistingVer HKCU "${UNINST_KEY}" "DisplayVersion"
  ${If} $Existing != ""
  ${AndIf} ${FileExists} "$Existing\RenderBatch.exe"
    ${If} $Mode == "installed"
    ${AndIf} $INSTDIR == "$LOCALAPPDATA\Programs\${APP}"     ; not when /D= picked a folder
      StrCpy $INSTDIR $Existing
    ${EndIf}
  ${Else}
    StrCpy $Existing ""
  ${EndIf}
  ${If} $Mode == "portable"
  ${AndIf} $INSTDIR == "$LOCALAPPDATA\Programs\${APP}"
    StrCpy $INSTDIR "$DESKTOP\RenderBatch"
  ${EndIf}
FunctionEnd

Function TypePage
  !insertmacro MUI_HEADER_TEXT "$(T_TYPE_TITLE)" "$(T_TYPE_SUB)"
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateRadioButton} 0 0 100% 12u "$(T_INSTALL)"
  Pop $RadInstall
  ${NSD_CreateLabel} 14u 14u -14u 24u "$(T_INSTALL_D)"
  Pop $0
  ${NSD_CreateRadioButton} 0 42u 100% 12u "$(T_PORTABLE)"
  Pop $RadPortable
  ${NSD_CreateLabel} 14u 56u -14u 24u "$(T_PORTABLE_D)"
  Pop $0
  ${NSD_CreateCheckbox} 0 88u 100% 12u "$(T_DESKTOP)"
  Pop $ChkDesktop
  ${NSD_CreateCheckbox} 0 102u 100% 12u "$(T_STARTMENU)"
  Pop $ChkStart
  ${If} $Existing != ""
    ${NSD_CreateLabel} 0 122u 100% 20u "$(T_EXISTING) $ExistingVer: $Existing"
    Pop $0
  ${EndIf}
  ${NSD_OnClick} $RadInstall TypeClicked
  ${NSD_OnClick} $RadPortable TypeClicked
  ${If} $Mode == "portable"
    ${NSD_Check} $RadPortable
  ${Else}
    ${NSD_Check} $RadInstall
  ${EndIf}
  ${If} $WantDesktop == "1"
    ${NSD_Check} $ChkDesktop
  ${EndIf}
  ${If} $WantStart == "1"
    ${NSD_Check} $ChkStart
  ${EndIf}
  Call TypeClicked
  nsDialogs::Show
FunctionEnd

Function TypeClicked
  Pop $0   ; the clicked control (when called from a click)
  ${NSD_GetState} $RadPortable $1
  ${If} $1 == ${BST_CHECKED}
    EnableWindow $ChkStart 0
  ${Else}
    EnableWindow $ChkStart 1
  ${EndIf}
FunctionEnd

Function TypeLeave
  StrCpy $0 $Mode
  ${NSD_GetState} $RadPortable $1
  ${If} $1 == ${BST_CHECKED}
    StrCpy $Mode "portable"
  ${Else}
    StrCpy $Mode "installed"
  ${EndIf}
  ${NSD_GetState} $ChkDesktop $WantDesktop
  ${NSD_GetState} $ChkStart $WantStart
  ${If} $Mode == "portable"
    StrCpy $WantStart "0"
  ${EndIf}
  ; a sensible folder for the chosen way, unless one was picked already
  ${If} $0 != $Mode
  ${AndIf} $UserPickedDir == ""
    ${If} $Mode == "portable"
      StrCpy $INSTDIR "$DESKTOP\RenderBatch"
    ${ElseIf} $Existing != ""
      StrCpy $INSTDIR $Existing
    ${Else}
      StrCpy $INSTDIR "$LOCALAPPDATA\Programs\${APP}"
    ${EndIf}
  ${EndIf}
FunctionEnd

Function DirPre
  ${If} $Mode == "portable"
    !insertmacro MUI_HEADER_TEXT "$(T_DIR_P_TITLE)" "$(T_DIR_P_SUB)"
  ${EndIf}
FunctionEnd

Function DirLeave
  StrCpy $UserPickedDir "1"
  ; the app keeps its settings in its own folder, so the folder must be writable without admin rights
  ClearErrors
  CreateDirectory "$INSTDIR"
  FileOpen $0 "$INSTDIR\.write_test" w
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONEXCLAMATION "$(T_NOWRITE)"
    Abort
  ${EndIf}
  FileClose $0
  Delete "$INSTDIR\.write_test"
  !insertmacro CHECK_RUNNING
FunctionEnd


; ---------------------------------------------------------------------------------------------- install

Section "RenderBatch" SecMain
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  DetailPrint "$(T_COPYING)"
  File /r "${SRC}\*.*"
  CreateDirectory "$INSTDIR\presets"
  CreateDirectory "$INSTDIR\looks"
  CreateDirectory "$INSTDIR\luts"
  CreateDirectory "$INSTDIR\models"

  ; how it was installed (the app shows it in Settings and updates the Apps entry after in-app updates)
  WriteINIStr "$INSTDIR\install.ini" "install" "mode" "$Mode"      ; read by the uninstaller
  FileOpen $0 "$INSTDIR\install.json" w
  FileWrite $0 '{"mode": "$Mode", "version": "${VERSION}"}'
  FileClose $0

  ; the language picked for the installer (setup dialogs, and the app on a first install)
  StrCpy $R6 "en"
  ${If} $LANGUAGE == 1049
    StrCpy $R6 "ru"
  ${ElseIf} $LANGUAGE == 1048
    StrCpy $R6 "ro"
  ${EndIf}

  ; Python packages and big models: from the .rbdata files or an existing RenderBatch folder
  StrCpy $SetupOk "1"
  ${If} ${FileExists} "$INSTDIR\setup_manifest.json"
    DetailPrint "$(T_FINISHING)"
    System::Call 'kernel32::SetEnvironmentVariableW(w "RENDERBATCH_HOME", w "$INSTDIR")'
    System::Call 'kernel32::SetEnvironmentVariableW(w "PYTHONHOME", p 0)'
    System::Call 'kernel32::SetEnvironmentVariableW(w "PYTHONPATH", p 0)'
    StrCpy $R8 ""
    ${If} ${Silent}
      StrCpy $R8 " --auto"
    ${EndIf}
    ExecWait '"$INSTDIR\runtime\pythonw.exe" -E -s "$INSTDIR\app\start.py" --setup-only --lang $R6$R8 --source "$EXEDIR"' $0
    ${If} $0 != 0
      StrCpy $SetupOk "0"
      MessageBox MB_OK|MB_ICONINFORMATION "$(T_SETUP_LATER)" /SD IDOK
    ${EndIf}
  ${EndIf}

  ; first install: start the app in that language (settings copied from an older RenderBatch win)
  ${IfNot} ${FileExists} "$INSTDIR\settings.json"
    FileOpen $0 "$INSTDIR\settings.json" w
    FileWrite $0 '{"ui": {"language": "$R6"}}'
    FileClose $0
  ${EndIf}

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ${If} $Mode == "installed"
    WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP}"
    WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
    WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${APP}"
    WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\RenderBatch.exe"
    WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
    WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
    WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" "$0"
    ${If} $WantStart == ${BST_CHECKED}
      CreateShortCut "$SMPROGRAMS\${APP}.lnk" "$INSTDIR\RenderBatch.exe" "" "$INSTDIR\RenderBatch.exe" 0
    ${EndIf}
    ${If} $WantDesktop == ${BST_CHECKED}
      CreateShortCut "$DESKTOP\${APP}.lnk" "$INSTDIR\RenderBatch.exe" "" "$INSTDIR\RenderBatch.exe" 0
    ${EndIf}
  ${Else}
    ${If} $WantDesktop == ${BST_CHECKED}
      CreateShortCut "$DESKTOP\${APP} (portable).lnk" "$INSTDIR\RenderBatch.exe" "" "$INSTDIR\RenderBatch.exe" 0
    ${EndIf}
  ${EndIf}
SectionEnd

; ---------------------------------------------------------------------------------------------- uninstall

Function un.onInit
  !insertmacro MUI_UNGETLANGUAGE
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  !insertmacro CHECK_RUNNING

  ReadINIStr $R5 "$INSTDIR\install.ini" "install" "mode"

  ; program files (everything that comes with the app or is made by it again)
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\cache"
  RMDir /r "$INSTDIR\_update"
  Delete "$INSTDIR\RenderBatch.exe"
  Delete "$INSTDIR\README.txt"
  Delete "$INSTDIR\README_RU.txt"
  Delete "$INSTDIR\README_RO.txt"
  Delete "$INSTDIR\MODELS.txt"
  Delete "$INSTDIR\install.json"
  Delete "$INSTDIR\install.ini"
  Delete "$INSTDIR\setup_manifest.json"
  Delete "$INSTDIR\setup_done.json"
  Delete "$INSTDIR\crash.log"
  Delete "$INSTDIR\update.log"
  Delete "$INSTDIR\luts\README.txt"

  ; your own files: settings, presets, looks, LUTs and models (kept unless you say otherwise)
  ${IfNot} ${Silent}
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "$(T_DEL_MINE)" IDNO keep_mine
    RMDir /r "$INSTDIR\presets"
    RMDir /r "$INSTDIR\looks"
    RMDir /r "$INSTDIR\luts"
    RMDir /r "$INSTDIR\models"
    Delete "$INSTDIR\settings.json"
    keep_mine:
  ${EndIf}

  ; shortcuts and the Apps entry belong to this copy only if the registry points here
  ${If} $R5 == "installed"
    Delete "$SMPROGRAMS\${APP}.lnk"
    Delete "$DESKTOP\${APP}.lnk"
    DeleteRegKey HKCU "${UNINST_KEY}"
  ${Else}
    Delete "$DESKTOP\${APP} (portable).lnk"
  ${EndIf}

  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR\presets"
  RMDir "$INSTDIR\looks"
  RMDir "$INSTDIR\luts"
  RMDir "$INSTDIR\models\denoise"
  RMDir "$INSTDIR\models\upscale"
  RMDir "$INSTDIR\models\grain"
  RMDir "$INSTDIR\models"
  RMDir "$INSTDIR"         ; only when empty (your files stay if you kept them)
SectionEnd
