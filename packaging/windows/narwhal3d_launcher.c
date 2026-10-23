/* Narwhal 3D Windows portable launcher. GPL-3.0-or-later. */
#include <windows.h>
#include <shellapi.h>
#include <wchar.h>
#include <stdlib.h>
#include <string.h>

#ifndef NARWHAL3D_VERSION
#define NARWHAL3D_VERSION L"development"
#endif

struct buffer {
  wchar_t *text;
  size_t used;
  size_t capacity;
};

static int reserve(struct buffer *b, size_t extra)
{
  size_t need = b->used + extra + 1;
  if (need <= b->capacity) return 1;
  size_t capacity = b->capacity ? b->capacity : 1024;
  while (capacity < need) capacity *= 2;
  wchar_t *next = (wchar_t *)realloc(b->text, capacity * sizeof(wchar_t));
  if (!next) return 0;
  b->text = next;
  b->capacity = capacity;
  return 1;
}

static int append_text(struct buffer *b, const wchar_t *text)
{
  size_t n = wcslen(text);
  if (!reserve(b, n)) return 0;
  memcpy(b->text + b->used, text, n * sizeof(wchar_t));
  b->used += n;
  b->text[b->used] = L'\0';
  return 1;
}

/* Quote one argument using the CommandLineToArgvW / Microsoft CRT rules. */
static int append_argument(struct buffer *b, const wchar_t *arg)
{
  if (b->used && !append_text(b, L" ")) return 0;
  int quote = !arg[0] || wcspbrk(arg, L" \t\n\v\"") != NULL;
  if (!quote) return append_text(b, arg);
  if (!append_text(b, L"\"")) return 0;
  size_t slashes = 0;
  for (const wchar_t *p = arg;; ++p) {
    if (*p == L'\\') {
      ++slashes;
      continue;
    }
    if (*p == L'\"') {
      for (size_t i = 0; i < slashes * 2 + 1; ++i) if (!append_text(b, L"\\")) return 0;
      if (!append_text(b, L"\"")) return 0;
      slashes = 0;
      continue;
    }
    if (*p == L'\0') {
      for (size_t i = 0; i < slashes * 2; ++i) if (!append_text(b, L"\\")) return 0;
      break;
    }
    for (size_t i = 0; i < slashes; ++i) if (!append_text(b, L"\\")) return 0;
    slashes = 0;
    wchar_t one[2] = {*p, L'\0'};
    if (!append_text(b, one)) return 0;
  }
  return append_text(b, L"\"");
}

static void show_error(const wchar_t *message)
{
  MessageBoxW(NULL, message, L"Narwhal 3D could not start", MB_OK | MB_ICONERROR);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show)
{
  (void)instance; (void)previous; (void)command_line; (void)show;
  wchar_t root[32768];
  DWORD n = GetModuleFileNameW(NULL, root, (DWORD)(sizeof(root) / sizeof(root[0])));
  if (!n || n >= sizeof(root) / sizeof(root[0])) {
    show_error(L"The launcher path is too long or unavailable.");
    return 2;
  }
  wchar_t *slash = wcsrchr(root, L'\\');
  if (!slash) {
    show_error(L"The launcher is not inside a portable Narwhal 3D folder.");
    return 2;
  }
  *slash = L'\0';

  wchar_t runtime[32768], bootstrap[32768], welcome[32768];
  if (_snwprintf(runtime, 32768, L"%ls\\runtime\\blender.exe", root) < 0 ||
      _snwprintf(bootstrap, 32768, L"%ls\\app\\launch\\bootstrap.py", root) < 0 ||
      _snwprintf(welcome, 32768,
                 L"%ls\\app\\runtime\\windows-x86_64\\bin\\narwhal-welcome.exe", root) < 0) {
    show_error(L"The portable Narwhal 3D path is too long.");
    return 2;
  }
  if (GetFileAttributesW(runtime) == INVALID_FILE_ATTRIBUTES ||
      GetFileAttributesW(bootstrap) == INVALID_FILE_ATTRIBUTES ||
      GetFileAttributesW(welcome) == INVALID_FILE_ATTRIBUTES) {
    show_error(L"The portable package is incomplete. Keep Narwhal3D.exe beside the app and runtime folders.");
    return 2;
  }

  int argc = 0;
  wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
  if (!argv) {
    show_error(L"Windows could not parse the command line.");
    return 2;
  }
  struct buffer command = {0};
  wchar_t test_mode[32] = {0};
  GetEnvironmentVariableW(L"NARWHAL3D_LAUNCHER_TEST", test_mode, 32);
  const wchar_t *launch_program = runtime;
  if (wcscmp(test_mode, L"version") == 0) {
    if (!append_argument(&command, runtime) || !append_argument(&command, L"--version")) {
      goto memory_error;
    }
  }
  else {
    const wchar_t *fixed[] = {welcome, L"--runtime", runtime, L"--bootstrap", bootstrap, L"--"};
    if (wcscmp(test_mode, L"headless") == 0) {
      const wchar_t *headless[] = {
          runtime, L"-b", L"--factory-startup", L"--python-exit-code", L"1",
          L"--python", bootstrap};
      for (size_t i = 0; i < sizeof(headless) / sizeof(headless[0]); ++i) {
        if (!append_argument(&command, headless[i])) goto memory_error;
      }
    }
    else {
      launch_program = welcome;
      for (size_t i = 0; i < sizeof(fixed) / sizeof(fixed[0]); ++i) {
        if (!append_argument(&command, fixed[i])) goto memory_error;
      }
      for (int i = 1; i < argc; ++i) {
        if (!append_argument(&command, argv[i])) goto memory_error;
      }
    }
  }

  if (!SetEnvironmentVariableW(L"NARWHAL3D_PACKAGE_FORMAT", L"windows-portable") ||
      !SetEnvironmentVariableW(L"NARWHAL3D_WINDOWS", L"1") ||
      !SetEnvironmentVariableW(L"NARWHAL3D_VERSION", NARWHAL3D_VERSION) ||
      !SetEnvironmentVariableW(L"NARWHAL3D_PACKAGE_PATH", root)) {
    show_error(L"Windows could not initialize the portable package environment.");
    LocalFree(argv);
    free(command.text);
    return 3;
  }
  STARTUPINFOW startup = {sizeof(startup)};
  PROCESS_INFORMATION process = {0};
  if (!CreateProcessW(launch_program, command.text, NULL, NULL, FALSE,
                      CREATE_UNICODE_ENVIRONMENT, NULL, root, &startup, &process)) {
    DWORD code = GetLastError();
    wchar_t message[512];
    _snwprintf(message, 512, L"The bundled runtime did not start (Windows error %lu).", code);
    show_error(message);
    LocalFree(argv);
    free(command.text);
    return 3;
  }
  CloseHandle(process.hThread);
  WaitForSingleObject(process.hProcess, INFINITE);
  DWORD code = 1;
  GetExitCodeProcess(process.hProcess, &code);
  CloseHandle(process.hProcess);
  LocalFree(argv);
  free(command.text);
  return (int)code;

memory_error:
  show_error(L"There was not enough memory to construct the launch command.");
  LocalFree(argv);
  free(command.text);
  return 2;
}
