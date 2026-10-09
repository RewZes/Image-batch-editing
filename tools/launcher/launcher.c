/* RenderBatch.exe: starts runtime\pythonw.exe app\start.py from the folder this exe is in. */
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <wchar.h>

int WINAPI wWinMain(HINSTANCE h, HINSTANCE p, PWSTR args, int show) {
    wchar_t dir[MAX_PATH * 2], py[MAX_PATH * 2], cmd[MAX_PATH * 6];
    DWORD n = GetModuleFileNameW(NULL, dir, MAX_PATH * 2);
    if (n == 0 || n >= MAX_PATH * 2) return 1;
    wchar_t *slash = wcsrchr(dir, L'\\');
    if (slash) *slash = 0;

    swprintf(py, MAX_PATH * 2, L"%ls\\runtime\\pythonw.exe", dir);
    if (GetFileAttributesW(py) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL, L"The 'runtime' folder is missing.\nKeep RenderBatch.exe together with the runtime, app and models folders.",
                    L"RenderBatch", MB_ICONERROR);
        return 1;
    }
    swprintf(cmd, MAX_PATH * 6, L"\"%ls\" -E -s \"%ls\\app\\start.py\"", py, dir);
    SetEnvironmentVariableW(L"RENDERBATCH_HOME", dir);
    SetEnvironmentVariableW(L"PYTHONHOME", NULL);
    SetEnvironmentVariableW(L"PYTHONPATH", NULL);

    STARTUPINFOW si; PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof si); si.cb = sizeof si;
    ZeroMemory(&pi, sizeof pi);
    if (!CreateProcessW(py, cmd, NULL, NULL, FALSE, 0, NULL, dir, &si, &pi)) {
        MessageBoxW(NULL, L"Could not start the bundled Python runtime.", L"RenderBatch", MB_ICONERROR);
        return 1;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
