import sublime, sublime_plugin, os, sys, ctypes
from ctypes import wintypes

# --- Windows API access (ctypes) -------------------------------------------
# Every name below is a Windows constant or function. Each line says what it is.
GWL_EXSTYLE = -20            # index for "extended window style" in Get/SetWindowLong
WS_EX_LAYERED = 0x00080000   # style bit that lets a window have an opacity value
LWA_ALPHA = 0x00000002       # tells SetLayeredWindowAttributes to use the alpha number

# Type of the callback that EnumWindows calls once for every top-level window.
ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

def _load_user32():
    # Load user32.dll and declare argument/return types so 64-bit handles are not truncated.
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.EnumWindows.argtypes = [ENUM_WINDOWS_PROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]
    user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
    return user32

def set_window_transparency_nt(pid, alpha, app_title):
    """Set opacity (0-255) on every visible Sublime window, directly and immediately.

    Replaces the old external SetTransparency.exe: no process is started per change,
    so changes can never finish out of order or pile up.
    """
    user32 = _load_user32()
    alpha = max(1, min(255, int(round(alpha))))  # never 0: an invisible window is unusable
    changed = []

    def visit(hwnd, _lparam):
        owner_pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))  # which process owns it
        if owner_pid.value != pid or not user32.IsWindowVisible(hwnd):
            return True  # not ours, or hidden: keep enumerating
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)  # read the window title
        if app_title not in title.value:
            return True  # e.g. a popup without our title: leave alone
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if not style & WS_EX_LAYERED:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)  # allow opacity
        user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)  # apply the opacity
        changed.append(hwnd)
        return True

    user32.EnumWindows(ENUM_WINDOWS_PROC(visit), 0)
    print("Sublime window transparency is set to %d (%d window(s))" % (alpha, len(changed)))
    return bool(changed)

# --- Saving the setting -----------------------------------------------------
def save_soon():
    """Save the opacity once, 800 ms after the last change (not on every scroll tick)."""
    config.save_ticket += 1
    ticket = config.save_ticket
    def save_if_latest():
        if ticket != config.save_ticket:
            return  # a newer change arrived; that one will save
        config.settings.set('enabled', config.enabled)
        config.settings.set('alpha_percentage', config.alpha_per_current)
        sublime.save_settings('glassit.sublime-settings')
    sublime.set_timeout(save_if_latest, 800)

def update_window_transparency_nt():
    alpha = config.alpha_current if config.enabled else config.alpha_max
    if set_window_transparency_nt(config.st_pid, alpha, config.st_title):
        save_soon()

def plugin_loaded():
    settings = sublime.load_settings('glassit.sublime-settings')
    # Remove 'reload' hooks left by older versions of this file (hot-reload keeps them).
    settings.clear_on_change('reload')

    global config

    class config:
        def load(self):
            if (sublime.platform() == "windows"):
                config.settings = settings
                config.save_ticket = 0
                config.enabled = bool(settings.get('enabled', True))
                config.alpha_per_default = int(settings.get('alpha_percentage_default', 90))
                config.alpha_per_current = int(settings.get('alpha_percentage', config.alpha_per_default))
                config.alpha_step = int(settings.get('alpha_step', 5))
                # Lowest allowed opacity (percent). Hard minimum 5, never 0.
                config.alpha_per_min = max(5, min(100, int(settings.get('alpha_percentage_min', 20))))
                config.alpha_per_current = max(config.alpha_per_min, min(100, config.alpha_per_current))
                config.alpha_per_default = max(config.alpha_per_min, min(100, config.alpha_per_default))
                config.alpha_max = 255
                config.st_title = settings.get('st_title', "Sublime Text")
                config.delay = 5000

                config.alpha_current = config.alpha_max * config.alpha_per_current / 100

                if sys.version_info[0] == 2:
                    # ST2 load plugin within main process
                    config.st_pid = os.getpid()
                else:
                    # ST3 & ST4 load plugin in the child process "plugin_host.exe"
                    config.st_pid = os.getppid()
            else:
                print("Set transparency doesn't support this platform yet!")

    config = config()
    config.load()

    if (sublime.platform() == "windows"):
        # Delay set transparency until main window is created.
        sublime.set_timeout(update_window_transparency_nt, config.delay)
    else:
        print("Set transparency doesn't support this platform yet!")
    # No add_on_change('reload') hook on purpose: our own saves used to trigger a
    # re-read that could load an older value and make the opacity jump around.

if sys.version_info[0] == 2:
    plugin_loaded()

class ToggleTransparencyCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        config.enabled = not config.enabled
        update_window_transparency_nt()

    def is_checked(self, **args):
        return config.enabled

class ResetTransparencyCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        if(config.enabled == True):
            config.alpha_per_current = config.alpha_per_default
            config.alpha_current = config.alpha_max * config.alpha_per_current / 100
            update_window_transparency_nt()

class IncreaseTransparencyCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        if(config.enabled == True):
            config.alpha_per_current = config.alpha_per_current - config.alpha_step
            if(config.alpha_per_current < config.alpha_per_min):
                config.alpha_per_current = config.alpha_per_min
            config.alpha_current = config.alpha_max * config.alpha_per_current / 100
            update_window_transparency_nt()

class DecreaseTransparencyCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        if(config.enabled == True):
            config.alpha_per_current = config.alpha_per_current + config.alpha_step
            if(config.alpha_per_current > 100):
                config.alpha_per_current = 100
            config.alpha_current = config.alpha_max * config.alpha_per_current / 100
            update_window_transparency_nt()
